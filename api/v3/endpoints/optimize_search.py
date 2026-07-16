"""v3 optimize-search endpoint — mirrors the parent repo's api/v2/endpoints/optimize_search.py
but runs advanced_search_optimization_v3.single_process (MULTI-ARCHETYPE: derives the JD's
candidate archetypes, optimizes EACH toward the target band, widens thin ones, unions them).

Differences vs v2:
  * single_process receives job_desc, enabling the JD-driven multi-archetype path;
  * the response additionally carries `archetypes` (one optimized condition per archetype,
    incl. widened variants), `n_archetypes` and `linkedin_count_calls`;
  * no LinkedIn-APN highlight generation (the standalone deploy has no highlight service —
    an `apn_display_value` field in the request is accepted and ignored).
"""
import sys
import os
from fastapi import APIRouter, HTTPException
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from api.v3.schemas import OptimizeSearchRequest, OptimizeSearchResponse, ErrorResponse
from advanced_search_optimization_v3 import (
    single_process,
    reset_linkedin_call_count,
    get_linkedin_call_count,
)

router = APIRouter()


@router.post("/optimize-search", response_model=OptimizeSearchResponse, responses={500: {"model": ErrorResponse}})
def optimize_search_conditions(request: OptimizeSearchRequest):
    """
    v3 MULTI-ARCHETYPE search optimization (advanced_search_optimization_v3.single_process)

    Input Parameters:
    - parsing_result: Full parsing result from the parsing endpoint (required)
    - job_id: Job ID for tracking (optional, default: "")
    - search_channel: "recruiter" (default) or "sales_nav"
    - min_target / max_target: acceptable result-count band (default 200/600)
    - include_language_in_search: add language to the API search conditions (default True)

    Output: merged optimized search conditions PLUS per-archetype conditions (`archetypes`)
    so a caller can fetch each archetype separately and union the results for recall.
    """
    try:
        parsing_result = request.parsing_result

        print(f"[{datetime.now()}] Starting v3 multi-archetype search optimization...")
        print(f"  Job ID: {request.job_id}")
        print(f"  Search channel: {request.search_channel}")

        # ===== Extract data from parsing result =====

        # Job title - take top 2 titles (align with whole_pipeline_validation.py)
        job_title_dict = parsing_result.get("job_title_info", {})
        job_title_list = []
        if job_title_dict and job_title_dict.get("title"):
            title_list = job_title_dict["title"]
            for t in title_list[:2]:
                if isinstance(t, (list, tuple)):
                    job_title_list.append(t[0])
                else:
                    job_title_list.append(t)

        # Job function - take top function only
        job_function_dict = parsing_result.get("job_function_info", {})
        job_function_list = []
        if job_function_dict and job_function_dict.get("function"):
            function_list = job_function_dict["function"]
            if function_list and len(function_list) > 0:
                top_function = function_list[0][0] if isinstance(function_list[0], (list, tuple)) else function_list[0]
                job_function_list = [top_function]

        # Industry - take top industry only
        company_industry_dict = parsing_result.get("company_industry_info", {})
        industry_list = []
        if company_industry_dict and company_industry_dict.get("industry"):
            industry_data = company_industry_dict["industry"]
            if industry_data and len(industry_data) > 0:
                top_industry = industry_data[0][0] if isinstance(industry_data[0], (list, tuple)) else industry_data[0]
                industry_list = [top_industry]

        # Seniority and year of experience
        job_seniority_dict = parsing_result.get("job_seniority_info", {})
        seniority_list = []
        initial_year_of_experience = None

        suggest_min_year = parsing_result.get("suggest_min_year")
        suggest_max_year = parsing_result.get("suggest_max_year")
        job_required_min_years = parsing_result.get("job_required_min_years")
        job_required_max_years = parsing_result.get("job_required_max_years")

        year_min = suggest_min_year if suggest_min_year is not None else (job_required_min_years or 0)
        year_max = suggest_max_year if suggest_max_year is not None else (job_required_max_years or 30)
        if year_min or year_max:
            initial_year_of_experience = {"start_num_year": year_min, "end_num_year": year_max}
            print(f"[{datetime.now()}] Year of experience set to: min={year_min}, max={year_max}")

        if job_seniority_dict:
            seniority_data = job_seniority_dict.get("seniority", [])
            if seniority_data and len(seniority_data) > 0:
                top_seniority = seniority_data[0][0] if isinstance(seniority_data[0], (list, tuple)) else seniority_data[0]
                seniority_list = [top_seniority]

        job_location_list = parsing_result.get("job_location_list", [])
        job_search_language = parsing_result.get("job_search_language", [])
        mandatory_skills = parsing_result.get("mandatory_skills", [])

        # ===== Build initial_conditions =====
        initial_conditions = {}
        if industry_list:
            initial_conditions["industry"] = industry_list
        if job_function_list:
            initial_conditions["job_function"] = job_function_list
        if job_title_list:
            initial_conditions["job_title"] = job_title_list
        if job_location_list:
            initial_conditions["location"] = {"name": job_location_list}
        if seniority_list:
            initial_conditions["seniority"] = seniority_list
        if initial_year_of_experience:
            initial_conditions["year_of_experience"] = initial_year_of_experience
        if job_search_language and request.include_language_in_search:
            initial_conditions["language"] = job_search_language
            print(f"[{datetime.now()}] Language included in search conditions: {job_search_language}")
        elif job_search_language:
            print(f"[{datetime.now()}] Language filtering disabled in search API (post-search only)")

        prefer_candidate_companies = parsing_result.get("prefer_candidate_companies", [])
        if prefer_candidate_companies:
            if isinstance(prefer_candidate_companies, str):
                companies_list = [c.strip() for c in prefer_candidate_companies.split(",") if c.strip()]
            else:
                companies_list = prefer_candidate_companies
            if companies_list:
                initial_conditions["companies"] = companies_list
                print(f"[{datetime.now()}] Preferred companies added to search conditions: {companies_list}")

        # ===== Build relaxation_options =====
        relaxation_options = {}
        if job_function_dict and job_function_dict.get("function"):
            relaxation_options["job_function"] = job_function_dict["function"]
        if job_title_dict and job_title_dict.get("title"):
            relaxation_options["job_title"] = job_title_dict["title"]
        if job_seniority_dict and job_seniority_dict.get("seniority"):
            relaxation_options["seniority"] = job_seniority_dict["seniority"]
        if company_industry_dict and company_industry_dict.get("industry"):
            relaxation_options["industry"] = company_industry_dict["industry"]
        if job_location_list:
            relaxation_options["location"] = job_location_list

        # JD text: enables v3's JD-driven multi-archetype path (baseline + personas).
        job_desc = parsing_result.get("job_desc") or "\n\n".join(
            str(parsing_result.get(k) or "") for k in
            ("jd_summary", "jd_responsibilities", "jd_requirements")).strip()

        print(f"[{datetime.now()}] Initial conditions: {initial_conditions}")
        print(f"[{datetime.now()}] Mandatory skills: {mandatory_skills}")
        print(f"[{datetime.now()}] Relaxation options keys: {list(relaxation_options.keys())}")
        print(f"[{datetime.now()}] Optimization targets: min={request.min_target}, max={request.max_target}")
        print(f"[{datetime.now()}] JD-driven multi-archetype mode: {bool(job_desc)}")

        # ===== Call v3 single_process optimization =====
        reset_linkedin_call_count()
        result_info = single_process(
            initial_conditions=initial_conditions,
            mandatory_skills=mandatory_skills,
            relaxation_options=relaxation_options,
            min_target=request.min_target,
            max_target=request.max_target,
            job_desc=job_desc or None,
        )
        linkedin_count_calls = get_linkedin_call_count()

        final_skills = result_info.get('final_skills') or []
        final_conditions = result_info.get('final_conditions') or {}
        readable_optim_path = result_info.get('rewritten_optim_path') or ""
        format_filter_conditions = result_info.get('format_filter_conditions') or {}
        final_count = result_info.get('final_count', 0)
        archetypes = result_info.get('archetypes') or []

        print(f"[{datetime.now()}] Optimization completed")
        print(f"  Archetypes: {len(archetypes)}")
        print(f"  Final skills: {final_skills}")
        print(f"  Final count: {final_count}")
        print(f"  LinkedIn count-probe calls: {linkedin_count_calls}")

        _llm_provider = result_info.get("llm_provider", "openai")
        _llm_model = result_info.get("llm_model", "gpt-4.1")

        return OptimizeSearchResponse(
            # Optimized search conditions
            final_skills=final_skills,
            final_conditions=final_conditions,
            final_count=final_count,
            format_filter_conditions=format_filter_conditions,
            rewritten_optim_path=readable_optim_path,

            # v3 multi-archetype additions
            archetypes=archetypes,
            n_archetypes=len(archetypes),
            linkedin_count_calls=linkedin_count_calls,

            # Pass-through for batch-search compatibility
            job_id=request.job_id,
            job_desc=parsing_result.get("job_desc", ""),
            job_summary=parsing_result.get("job_summary"),
            job_location_list=job_location_list,
            job_search_language=job_search_language,
            prefer_candidate_companies=parsing_result.get("prefer_candidate_companies"),
            hiring_company=parsing_result.get("hiring_company"),
            all_skills=parsing_result.get("all_skills"),
            mandatory_skills=mandatory_skills,
            good_to_have_skills=parsing_result.get("good_to_have_skills"),
            job_required_main_skills_list=final_skills,
            search_channel=request.search_channel,

            # Year requirements pass-through
            job_required_min_years=parsing_result.get("job_required_min_years"),
            job_required_max_years=parsing_result.get("job_required_max_years"),
            suggest_min_year=parsing_result.get("suggest_min_year"),
            suggest_max_year=parsing_result.get("suggest_max_year"),

            # search-candidates compatibility
            job_main_skills=final_skills,
            search_results_num=final_count,

            # Metadata
            total_input_tokens=result_info.get("total_input_tokens", 0),
            total_output_tokens=result_info.get("total_output_tokens", 0),
            run_time_stats=parsing_result.get("run_time_stats", {}),
            llm_provider=_llm_provider,
            llm_model=_llm_model,

            # Diagnostics
            errors=None,
            trace=None,
            message=f"v3 multi-archetype optimization completed. "
                    f"{len(archetypes)} archetype(s), merged count: {final_count}",
        )

    except Exception as e:
        print(f"[ERROR] Unexpected error in v3 optimize search: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during search optimization: {str(e)}"
        )
