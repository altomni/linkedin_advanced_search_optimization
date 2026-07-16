# Standard library imports
import ast
import os
import re
import json
import time

from urllib.parse import quote
import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv

from llms.deepseek import DeepSeekWrapper
from llms.qwen import QwenWrapper
from prompts import jd_extraction_prompt_template, job_skills_understanding_prompt, job_skills_synonym_prompt, \
    extract_all_skills_prompt

# Local imports
from utils.general_utils import (
    docx_to_text,
    extract_json_block,
    extract_json_markdown_block,
    json_output_validator,
    load_json_string,
)
from config.config import DEFAULT_LLM_PARSE_MAX_RETRIES

# Load environment variables
load_dotenv()


def llm_search_rerank(llm, query, candidates):
    prompt_template = """
    You are an expert semantic reranker. 
    Given (a) a user search query and (b) a JSON array of candidate records, 
    return ONLY the records that are highly relevant to the query, give probability of relevancy of each record and sorted from most to least relevant by probability of relevancy

    Guidelines
    • Relevance is judged by meaning, not exact wording (e.g., “AI” ≈ “Artificial Intelligence”).  
    • Keep a record if its displayValue clearly matches or is a direct specialization of the query.  
    • Discard partial or loosely related terms.  
    • Preserve the original {{displayValue, id}} pairs exactly—do NOT add, remove, or rename keys.  
    • Output must be valid JSON **array** (no extra keys, no comments, no markdown).  
    • If nothing is relevant, output `[]`.
    • Give probability of relevancy and named as "relevancy"

    ---

    Example query: "AI Robot Engineer"
    Example candidates: ```[{{'displayValue': 'Test Engineer', 'id': '264'}}, {{'displayValue': 'Robotic Engineer', 'id': '31823'}}, {{'displayValue': 'Artificial Intelligence Engineer', 'id': '30128'}}]```
    Example output: ```[{{'displayValue': 'Artificial Intelligence Engineer', 'id': '30128', 'relevancy': 0.60}}, {{'displayValue': 'Robotic Engineer', 'id': '31823', 'relevancy': 0.40}}]```

    ---

    Query: {query}
    Candidates: {candidates}

            """
    prompt = prompt_template.format(query=query, candidates=str(candidates))
    response, input_tokens, output_tokens = llm.invoke(prompt, temperature=0.5)
    return response, input_tokens, output_tokens


def job_title_adjustment(llm, job_title: str, tried_job_title_list: list = []) -> tuple[str, int, int]:
    job_title_adjustment_prompt = """
        Rewrite the job title to the single closest canonical title.

        Rules:
            - Exclude any titles already tried
            - Use semantic similarity (synonyms, abbreviations) over pure spelling.
            - Strip noise (company/team/location), tools, and tech stacks unless essential to role identity.
            - Keep role/function (Engineer/Manager/Designer/Analyst/etc.).
            - Keep specialization if core (Backend/Frontend/ML/Data/iOS/Android/DevOps/QA/Product/UX, etc.).
            - Prefer the shortest, widely used American English title.
            - If multiple tie, choose: (1) closest role function, (2) closest specialization, (3) more general.

        Output:
            Return one title string only, no quotes or extra text.

        Example:
            Input: Backend Software Engineer → Output: Backend Engineer.

        ---

        Provided title: {job_title}

        ---

        Tried titles: {tried_job_title_list}

       """

    response, input_tokens, output_tokens = llm.invoke(
        job_title_adjustment_prompt.format(job_title=job_title, tried_job_title_list=tried_job_title_list),
        temperature=0.01)
    return response, input_tokens, output_tokens


def jd_basic_understanding_process(llm, job_desc, model_type=None, thinking_budget=None):
    """
    Process a job description to extract structured information using LLM.

    Args:
        llm: LLM wrapper instance (must expose `.invoke(prompt, **kwargs)` returning (text, in_tok, out_tok)).
        job_desc (str): Full job description text.
        model_type (str | None): Optional model override. If None, the wrapper's default model is used.
        thinking_budget (int | None): Optional thinking-token budget (GeminiWrapper only). If None, omit.

    Returns:
        tuple: (job_info_dict, input_tokens, output_tokens)
    """
    internal_start_time = time.time()
    # 1. Format the prompt template with the job description
    jd_extraction_prompt = jd_extraction_prompt_template.format(
        job_description=job_desc
    )
    # Always run with the JSON validator + retries so malformed outputs (common with Gemini
    # when thinking_budget > 0 or when it wraps JSON in prose) trigger a retry instead of
    # silently producing an empty dict that breaks every downstream extraction.
    invoke_kwargs = {
        "temperature": 0.1,
        "validator": json_output_validator,
        "max_retries": DEFAULT_LLM_PARSE_MAX_RETRIES,
    }
    if model_type is not None:
        invoke_kwargs["model_type"] = model_type
    if thinking_budget is not None:
        invoke_kwargs["thinking_budget"] = thinking_budget
    response, input_tokens, output_tokens = llm.invoke(jd_extraction_prompt, **invoke_kwargs)

    # Robust JSON extraction: handles fenced blocks, truncated fences, bare JSON, and
    # JSON embedded in surrounding prose (all four strategies in extract_json_block).
    try:
        job_info = extract_json_block(response)
    except ValueError as e:
        print(f"[jd_basic_understanding_process] Hardened JSON parse failed: {e}")
        print(f"[jd_basic_understanding_process] Raw response head: {response[:500] if response else '<empty>'}")
        # Fall back to the old fragile path as a last-ditch attempt (matches legacy behavior).
        if response and "```json" in response:
            json_str = (extract_json_markdown_block(response) or "").strip()
        else:
            json_str = (response or "").strip()
        job_info = load_json_string(json_str) or {}
    # jd_understanding = json_obj.get("jd_understanding")

    jd_extract_done_time = time.time()
    print("Jd extraction run time: ", jd_extract_done_time - internal_start_time)

    return job_info, input_tokens, output_tokens


def job_skill_understanding_process(
    llm, job_desc, model_type="gpt-4.1", temperature=0.25
):
    """
    Synchronous version - for backward compatibility

    This function extracts skills sequentially (slower).
    For better performance, use job_skill_understanding_process_async() instead.
    """
    section_input_tokens = 0
    section_output_tokens = 0

    start_time = time.time()

    # 1. Extract all skills (validator + retries + robust JSON extraction)
    all_skill_prompt = extract_all_skills_prompt.format(job_desc=job_desc)
    all_skills_str, input_tokens, output_tokens = llm.invoke(
        all_skill_prompt,
        model_type=model_type,
        temperature=temperature,
        validator=json_output_validator,
        max_retries=DEFAULT_LLM_PARSE_MAX_RETRIES,
    )

    try:
        all_skills_dict = extract_json_block(all_skills_str)
    except ValueError as e:
        print(f"[job_skill_understanding_process] all_skills JSON parse failed: {e}")
        print(f"[job_skill_understanding_process] Raw all_skills_str head: {all_skills_str[:500] if all_skills_str else '<empty>'}")
        fallback_str = all_skills_str or ""
        if "```json" in fallback_str:
            fallback_str = (extract_json_markdown_block(fallback_str) or "").strip()
        try:
            all_skills_dict = ast.literal_eval(fallback_str)
        except Exception as e2:
            print(f"[job_skill_understanding_process] Fallback literal_eval failed: {e2}")
            all_skills_dict = {"all_skills": []}

    all_skills_prob = [(record['skill'], record['probability']) for record in all_skills_dict.get('all_skills', [])]
    all_skills_prob.sort(key=lambda x: x[1], reverse=True)
    all_skills = [record[0] for record in all_skills_prob]

    section_input_tokens += input_tokens
    section_output_tokens += output_tokens

    # 2. Extract must-have and good-to-have skills (validator + retries + robust JSON extraction)
    jd_skill_prompt = job_skills_understanding_prompt.format(job_desc=job_desc)
    response, input_tokens, output_tokens = llm.invoke(
        jd_skill_prompt,
        model_type=model_type,
        temperature=temperature,
        validator=json_output_validator,
        max_retries=DEFAULT_LLM_PARSE_MAX_RETRIES,
    )

    section_input_tokens += input_tokens
    section_output_tokens += output_tokens

    try:
        skill_json_obj = extract_json_block(response)
    except ValueError as e:
        print(f"[job_skill_understanding_process] categorized-skills JSON parse failed: {e}")
        print(f"[job_skill_understanding_process] Raw response head: {response[:500] if response else '<empty>'}")
        if response and "```json" in response:
            json_str = (extract_json_markdown_block(response) or "").strip()
        else:
            json_str = (response or "").strip()
        skill_json_obj = load_json_string(json_str) or {}

    mandatory_skills = skill_json_obj.get("mandatory_skills")
    good_to_have_skills = skill_json_obj.get("good_to_have_skills")

    print("Skill extraction run time: ", time.time() - start_time)
    return all_skills, mandatory_skills, good_to_have_skills, section_input_tokens, section_output_tokens


async def job_skill_understanding_process_async(
    llm, job_desc, model_type="gpt-4.1", temperature=0.25
):
    """
    Asynchronous parallel version - FASTER!

    Executes two independent LLM calls in parallel:
    1. Extract all skills
    2. Extract mandatory vs good-to-have skills

    Performance: ~50% faster than sequential version (10s → 5s)
    """
    import asyncio

    section_input_tokens = 0
    section_output_tokens = 0

    start_time = time.time()

    # Prepare both prompts
    all_skill_prompt = extract_all_skills_prompt.format(job_desc=job_desc)
    jd_skill_prompt = job_skills_understanding_prompt.format(job_desc=job_desc)

    # Execute both LLM calls in parallel. Validator + retries guard against malformed
    # outputs (common with Gemini when thinking_budget > 0 or when JSON is wrapped in prose).
    async def _extract_all_skills():
        all_skills_str, input_tokens, output_tokens = await llm.ainvoke(
            all_skill_prompt,
            model_type=model_type,
            temperature=temperature,
            validator=json_output_validator,
            max_retries=DEFAULT_LLM_PARSE_MAX_RETRIES,
        )
        return all_skills_str, input_tokens, output_tokens

    async def _extract_categorized_skills():
        response, input_tokens, output_tokens = await llm.ainvoke(
            jd_skill_prompt,
            model_type=model_type,
            temperature=temperature,
            validator=json_output_validator,
            max_retries=DEFAULT_LLM_PARSE_MAX_RETRIES,
        )
        return response, input_tokens, output_tokens

    # Run both tasks in parallel
    (all_skills_str, in_tok1, out_tok1), (response, in_tok2, out_tok2) = await asyncio.gather(
        _extract_all_skills(),
        _extract_categorized_skills()
    )

    section_input_tokens += in_tok1 + in_tok2
    section_output_tokens += out_tok1 + out_tok2

    # Robust JSON extraction for all_skills: extract_json_block handles fenced blocks,
    # truncated fences, JSON embedded in surrounding prose, and bare JSON.
    try:
        all_skills_dict = extract_json_block(all_skills_str)
    except ValueError as e:
        print(f"[job_skill_understanding_process_async] all_skills JSON parse failed: {e}")
        print(f"[job_skill_understanding_process_async] Raw all_skills_str head: {all_skills_str[:500] if all_skills_str else '<empty>'}")
        # Legacy fallback (markdown strip + ast.literal_eval)
        fallback_str = all_skills_str or ""
        if "```json" in fallback_str:
            fallback_str = (extract_json_markdown_block(fallback_str) or "").strip()
        try:
            all_skills_dict = ast.literal_eval(fallback_str)
        except Exception as e2:
            print(f"[job_skill_understanding_process_async] Fallback literal_eval failed: {e2}")
            all_skills_dict = {"all_skills": []}

    all_skills_prob = [(record['skill'], record['probability']) for record in all_skills_dict.get('all_skills', [])]
    all_skills_prob.sort(key=lambda x: x[1], reverse=True)
    all_skills = [record[0] for record in all_skills_prob]

    # Robust JSON extraction for mandatory_skills / good_to_have_skills.
    try:
        skill_json_obj = extract_json_block(response)
    except ValueError as e:
        print(f"[job_skill_understanding_process_async] categorized-skills JSON parse failed: {e}")
        print(f"[job_skill_understanding_process_async] Raw response head: {response[:500] if response else '<empty>'}")
        # Legacy fallback
        if response and "```json" in response:
            json_str = (extract_json_markdown_block(response) or "").strip()
        else:
            json_str = (response or "").strip()
        skill_json_obj = load_json_string(json_str) or {}

    mandatory_skills = skill_json_obj.get("mandatory_skills")
    good_to_have_skills = skill_json_obj.get("good_to_have_skills")

    print("Skill extraction run time (PARALLEL): ", time.time() - start_time)
    return all_skills, mandatory_skills, good_to_have_skills, section_input_tokens, section_output_tokens


def job_skill_synonym_process(llm, job_skill):
    """Synchronous version - for backward compatibility"""
    start_time = time.time()
    skill_synonym_prompt = job_skills_synonym_prompt.format(job_skill=job_skill)
    response, input_tokens, output_tokens = llm.invoke(
        skill_synonym_prompt, model_type="gpt-4.1", temperature=0.5
    )
    synonym_list = load_json_string(response)
    print("Skill synonym run time: ", time.time() - start_time)
    return synonym_list, input_tokens, output_tokens


async def job_skill_synonym_process_async(llm, job_skill, model_type="gpt-4.1"):
    """Asynchronous version - for parallel execution"""
    start_time = time.time()
    skill_synonym_prompt = job_skills_synonym_prompt.format(job_skill=job_skill)
    response, input_tokens, output_tokens = await llm.ainvoke(
        skill_synonym_prompt, model_type=model_type, temperature=0.5
    )
    synonym_list = load_json_string(response)
    print("Skill synonym run time: ", time.time() - start_time)
    return synonym_list, input_tokens, output_tokens


if __name__ == "__main__":

    llm = DeepSeekWrapper()

    # Load job data from CSV
    jd_data = pd.read_csv("data/Backend_Jobs_Info.csv")

    # Fill missing values with empty strings to prevent NaN issues
    jd_data["title"].fillna("", inplace=True)
    jd_data["company_name"].fillna("", inplace=True)
    jd_data["description"].fillna("", inplace=True)
    jd_data["summary"].fillna("", inplace=True)
    jd_data["requirements"].fillna("", inplace=True)

    # Combine all job description fields into a single text column
    jd_data["raw_job_description"] = (
        jd_data["title"]
        + "\n"
        + jd_data["company_name"]
        + "\n"
        + jd_data["description"]
        + "\n"
        + jd_data["summary"]
        + "\n"
        + jd_data["requirements"]
    )

    # Initialize output list to store results
    output_list = []

    # Process each job description (limited to first 50 for testing)
    for idx, row in jd_data.iterrows():
        if idx >= 50:
            break  # Limit processing for testing purposes

        print(f"Processing job {idx}")

        # Extract job information
        job_id = row["job_id"]
        job_name = row["title"]
        job_desc = row["raw_job_description"]

        # Process job description using LLM
        (
            jd_understanding,
            job_title,
            job_function,
            job_industry,
            job_domain_field,
            job_seniority,
            job_location,
            job_required_language,
            mandatory_skills,
            good_to_have_skills,
        ) = jd_basic_understanding_process(job_id, job_name, job_desc)

        # Append results to output list
        output_list.append(
            [
                job_id,
                job_name,
                jd_understanding,
                job_title,
                job_function,
                job_industry,
                job_domain_field,
                job_seniority,
                job_location,
                job_required_language,
                mandatory_skills,
                good_to_have_skills,
            ]
        )

    # Create DataFrame from results
    output_df = pd.DataFrame(
        output_list,
        columns=[
            "job_id",
            "job_name",
            "jd_understanding",
            "job_title",
            "job_function",
            "job_industry",
            "job_domain_field",
            "job_seniority",
            "job_location",
            "job_required_language",
            "mandatory_skills",
            "good_to_have_skills",
        ],
    )

    # Merge with original data and save to CSV
    merged_output_df = jd_data.merge(output_df, on="job_id", how="left")
    merged_output_df.to_csv("../data/Jobs_Info_Software.csv", index=False)

    print(f"Processing complete. Results saved to ../data/Jobs_Info_Software.csv")
