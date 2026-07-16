import json
import os
import time

import numpy as np
import pandas as pd

from config.linkedin_enums import get_linkedin_enum_data
from jd_understanding_funcs import (
    jd_basic_understanding_process,
    job_skill_understanding_process,
    llm_search_rerank,
)
from linkedin_apiservice.management_service import LinkedInService
from llms.chatgpt import ChatGPTWrapper

from utils.general_utils import dedup_by_key, extract_json_markdown_block
from utils.jd_understanding_utils import multi_filters_to_str, convert_location_list
from utils.linkedin_formatter import convert_filters_to_sales_nav_conditions
from linkedin_recruiter_apiservice.api_service import RecruiterService
from utils.recruiter_api_formatter import convert_filters_to_recruiter_api_conditions
from utils.search_utils import (
    search_linkedin,
    batch_basic_linkedin_search,
    extract_candidate_info,
    improved_linkedin_search_api,
    extract_fixed_person_info,
)



# COST_NORMAL_INPUT_TOKEN = 0.4/int(1e6)
# COST_NORMAL_OUTPUT_TOKEN = 1.6/int(1e6)
COST_NORMAL_INPUT_TOKEN = 2.0 / int(1e6)
COST_NORMAL_OUTPUT_TOKEN = 8.0 / int(1e6)
COST_PRO_INPUT_TOKEN = 2.0 / int(1e6)
COST_PRO_OUTPUT_TOKEN = 8.0 / int(1e6)


def main_linkedin_search_process(raw_filters: dict, job_skills_list, llm, channel = "recruiter"):
    inner_input_tokens = 0
    inner_output_tokens = 0
    # Generate skills keywords str
    job_required_main_skills_str = ""
    for idx, skill in enumerate(job_skills_list):
        if idx > 0:
            job_required_main_skills_str += " OR "
        job_required_main_skills_str += f'"{skill}"'

    if channel == "recruiter":
        print(f" ===== Start to use channel: {channel} to search ========")
        linkedin_enum_params = get_linkedin_enum_data()

        # 1. Convert raw search conditions to Linkedin Recruiter condition
        print(f" ===== generate conditions for channel: {channel} ========")
        print(f"raw filters: {raw_filters}")
        try:
            recruiter_conditions = convert_filters_to_recruiter_api_conditions(
                raw_filters, job_skills_list, linkedin_enum_params, llm
            )
            print(f"===== conditions for channel (recruiter): {recruiter_conditions} ========")
        except Exception as e:
            import traceback
            print("[recruiter] convert_filters_to_recruiter_api_conditions failed:", e)
            print("[recruiter] traceback:\n", traceback.format_exc())
            raise

        print(f" ===== conditions for channel: {recruiter_conditions} ========")

        # naive contact filter string
        linkedin_format_filter_conditions = recruiter_conditions

        print("recruiter_api_conditions:", recruiter_conditions["filters"])

        input_tokens = recruiter_conditions["input_tokens"]
        output_tokens = recruiter_conditions["output_tokens"]

        print(f" ===== Use channel: {channel} to search ========")

        rs = RecruiterService()

        try:
            start_time = time.time()
            # search_results, input_tokens, output_tokens = rs.get_search_num(recruiter_conditions)
            search_results = rs.get_search_results(recruiter_conditions)
            # search_result_num = search_results['num']
            # print(f"search result num: {search_result_num}")
            print(f"run time: {time.time() - start_time} seconds")
        except Exception as e:
            import traceback
            print("[recruiter] get search results via recruiter API failed:", e)
            print("[recruiter] traceback:\n", traceback.format_exc())
            raise

        complete_search_conditions = recruiter_conditions

        print("recruiter_conditions: ", recruiter_conditions)
        try:
            job_title_filter = [
                sub_filter
                for key, sub_filter in recruiter_conditions["filters"].items()
                if key == "titles"
            ][0][0]
        except Exception as e:
            job_title_filter = {}
            print(
                "Error: job_title_filter not found in recruiter_conditions['filters']:", e
            )
        job_title_candidates = (
            job_title_filter["name"]
            if len(job_title_filter) > 0
            else raw_filters["job_title"][0]
            )

        print("==== Finish search via recruiter ====")


    elif channel == "sales_nav":
        import traceback
        linkedin_enum_params = get_linkedin_enum_data()
        linkedin_service = LinkedInService()

        print(f"[sales_nav] channel repr: {repr(channel)}")
        print(f"[sales_nav] raw_filters keys: {list(raw_filters.keys())}")
        # 1. Convert raw search conditions to LinkedIn search condition
        try:
            sales_nav_conditions = convert_filters_to_sales_nav_conditions(
                raw_filters, linkedin_service, linkedin_enum_params, llm
            )
        except Exception as e:
            print("[sales_nav] convert_filters_to_sales_nav_conditions failed:", e)
            print(traceback.format_exc())
            raise

        # naive contact filter string
        try:
            linkedin_format_filter_conditions = multi_filters_to_str(
                sales_nav_conditions["filters"]
            )
        except Exception as e:
            print("[sales_nav] multi_filters_to_str failed:", e)
            print("[sales_nav] filters sample:", sales_nav_conditions.get("filters"))
            print(traceback.format_exc())
            raise

        print("sales_nav_conditions:", sales_nav_conditions["filters"])

        try:
            search_results = search_linkedin(
                linkedin_format_filter_conditions, job_required_main_skills_str
            )
        except Exception as e:
            print("[sales_nav] search_linkedin failed:", e)
            print("[sales_nav] linkedin_format_filter_conditions: ", linkedin_format_filter_conditions)
            print("[sales_nav] job_required_main_skills_str: ", job_required_main_skills_str)
            print(traceback.format_exc())
            raise

        input_tokens = sales_nav_conditions["input_tokens"]
        output_tokens = sales_nav_conditions["output_tokens"]

        # search_data = {
        #     "filters": sales_nav_conditions,
        #     "keywords": job_required_main_skills_str,
        # }
        # search_results = linkedin_service.filter_query_search(data=search_data)
        # print("search_results:", search_results)
        # 组合完整的搜索条件
        complete_search_conditions = {
            "filters": sales_nav_conditions["filters"],
            "keywords": job_required_main_skills_str
        }

        try:
            job_title_filter = [
                sub_filter
                for sub_filter in sales_nav_conditions["filters"]
                if sub_filter["type"] == "CURRENT_TITLE"
            ][0]
        except Exception as e:
            job_title_filter = {}
            print(
                "Error: job_title_filter not found in sales_nav_conditions['filters']:", e
            )
        job_title_candidates = (
            job_title_filter["values"]
            if len(job_title_filter) > 0
            else raw_filters["job_title"][0]
        )

    inner_input_tokens += input_tokens
    inner_output_tokens += output_tokens

    # 2. Rerank job titles
    if len(job_title_candidates) > 1:
        job_titles = raw_filters.get("job_title")
        reranked_job_titles = []
        for job_title in job_titles:
            sub_reranked_job_titles_str, input_tokens, output_tokens = (
                llm_search_rerank(
                    llm=llm, query=job_title, candidates=job_title_candidates
                )
            )
            inner_input_tokens += input_tokens
            inner_output_tokens += output_tokens
            if "```json" in sub_reranked_job_titles_str:
                sub_reranked_job_titles_str = extract_json_markdown_block(
                    sub_reranked_job_titles_str
                )
            try:
                sub_reranked_job_titles = json.loads(sub_reranked_job_titles_str)
            except Exception as e:
                print("Error: json.loads(sub_reranked_candidates_str) failed:", e)
                print("sub_reranked_candidates_str:", sub_reranked_job_titles_str)
                sub_reranked_job_titles = []
            reranked_job_titles.extend(sub_reranked_job_titles)
    else:
        reranked_job_titles = job_title_candidates

    # print("reranked_job_candidates:", len(reranked_job_candidates), reranked_job_candidates)
    dedup_reranked_job_titles = dedup_by_key(reranked_job_titles, "text")
    # print("dedup_reranked_job_candidates:", len(dedup_reranked_job_candidates), dedup_reranked_job_candidates)



    # # ToDo
    # job_required_main_skills_str = '"Microservices" OR "Blockchain Integration"'
    # print("job_required_main_skills_str:", job_required_main_skills_str)

    print("return search results!!!!")
    return (
        search_results,
        dedup_reranked_job_titles,
        linkedin_format_filter_conditions,
        job_required_main_skills_str,
        inner_input_tokens,
        inner_output_tokens,
        complete_search_conditions,
    )


def choose_job_main_skills(mandatory_skills, pick_num=np.inf):
    total_prob = 0.0
    job_required_main_skills_list = []
    for idx, skill_sub_dict in enumerate(mandatory_skills):
        if pick_num == np.inf:
            if total_prob >= 0.5 or (
                    total_prob == 0 and len(job_required_main_skills_list) > 2
            ):
                break
        else:
            if idx >= pick_num:
                break
        if isinstance(skill_sub_dict, dict):
            skill = skill_sub_dict["skill"]
            prob = skill_sub_dict["probability"]
            total_prob += prob
        else:
            skill = skill_sub_dict
        job_required_main_skills_list.append(skill)

    return job_required_main_skills_list


if __name__ == "__main__":

    # raw_filters = {
    #     "job_function": ["Engineering", "Product Management"],
    #     "seniority": ["Director", "CXO"],
    #     "industry": ["Internet", "Software"],
    #     "job_title": ["CTO", "VP Engineering"],
    #     "companies": [
    #         {"company_name": "Google"},
    #         {"company_name": "Microsoft"}
    #     ],
    #     "location": {
    #         "name": ["San Francisco Bay Area", "New York"]
    #     },
    #     # "keywords": ["AI", "LLM"]
    # }

    # raw_filters = {
    #     "job_function": ["Engineering"],
    #     "seniority": ["Senior", "Strategic"],
    #     "industry": ["Software Development"],
    #     "job_title": ["ML Engineer", "AI Engineer", "Vision Engineer"],
    #     "companies": [{"company_name": "Google"}, {"company_name": "Microsoft"}],
    #     "location": {"name": ["San Francisco Bay Area"]},
    # }

    # raw_filters = {
    #     "job_function": ["Engineering"],
    #     "seniority": ["Senior", "Strategic"],
    #     "industry": ["Computer Hardware Manufacturing"],
    #     "job_title": ["Design Engineer", "Test Engineer"],
    #     "companies": [
    #         {"company_name": "Google"},
    #         {"company_name": "Microsoft"}
    #     ],
    #     "location": {
    #         "name": ["San Francisco Bay Area"]
    #     }
    # }

    # raw_filters = {
    #     "job_function": ["EE Circuit Engineer",],
    #     "seniority": ["Senior", "Strategic"],
    #     "industry": ["Computer Hardware Manufacturing"],
    #     "job_title": ["Design Engineer", "Test Engineer"],
    #     "companies": [
    #         {"company_name": "Apple"},
    #         {"company_name": "Nvidia"}
    #     ],
    #     "location": {
    #         "name": ["San Francisco Bay Area"]
    #     }
    # }

    # raw_filters = {
    #     # "job_function": ["EE Circuit Engineer",],
    #     # "seniority": ["Senior", "Strategic"],
    #     # "industry": ["Computer Hardware Manufacturing"],
    #     "job_title": ["AI Engineer", "Vision Engineer", "ML Engineer"],
    #     # "companies": [
    #     #     {"company_name": "Apple"},
    #     #     {"company_name": "Nvidia"}
    #     # ],
    #     "location": {
    #         "name": ["Bay Area"]
    #     }
    # }

    # raw_filters = {
    #     "job_function": ["Engineering"],
    #     "seniority": ["Senior", "Strategic"],
    #     "industry": ["Software Development"],
    #     "job_title": ["ML Engineer", "AI Engineer", "Vision Engineer"],
    #     "companies": [
    #         {"company_name": "Google"},
    #         {"company_name": "Microsoft"}
    #     ],
    #     "location": {
    #         "name": ["San Francisco Bay Area"]
    #     },
    #     # "keywords": ["Deep Learning", "LLM"]
    # }

    # raw_filters = {
    #     "job_function": ["Engineering"],
    #     "seniority": ["Senior"],
    #     "industry": ["Software Development"],
    #     "job_title": ["ML Engineer", "AI Engineer", "Vision Engineer"],
    #     "companies": [
    #         {"company_name": "Google"},
    #         {"company_name": "Microsoft"}
    #     ],
    #     "location": {
    #         "name": ["San Francisco Bay Area"]
    #     },
    # }

    # raw_filters = {'industry': ['Financial Services'], 'job_function': ['information technology'],
    #                'job_title': ['Software Engineer'],
    #                'location': {'name': ['Europe']}, # 'Europe', 'San Francisco Bay Area'
    #                'seniority': ['senior']}

    raw_filters = {
        "industry": ["Software Development"],
        "job_function": ["engineering"],
        "job_title": ["Senior Backend Engineer", "Platform Engineer"],
        "location": {"name": ["US", "Canada"]},
        "seniority": ["senior"],
    }

    {
        "industry": ["Software Development"],
        "job_function": ["engineering"],
        "job_title": ["Senior Backend Engineer", "Platform Engineer"],
        "location": {"name": ["US", "Canada"]},
        "seniority": ["senior"],
    }

    # job_required_main_skills_list = ['Java', 'Go', 'API design', 'Payments processing',
    #                                  'Cloud services (AWS, Google Cloud, Microsoft Azure)', 'SQL databases',
    #                                  'Kubernetes', 'Blockchain']  # 3
    # job_required_main_skills_list = ['Blockchain', 'API design', 'Payments processing',
    #                                  'Cloud services (AWS, Google Cloud, Microsoft Azure)']  # 0

    # job_required_main_skills_list = ['Blockchain', 'API design', 'Payments processing']  # 1053
    # job_required_main_skills_list = ['API design', 'Blockchain protocols', 'Java/Go', 'Microservices architecture', 'AWS/Google Cloud/Microsoft Azure']  # 1889
    # job_required_main_skills_list = ['API design', 'Blockchain protocols', 'Java/Go', 'Microservices architecture']  # 1889
    job_required_main_skills_list = [
        "Golang",
        "Distributed Systems",
        "Workflow Automation",
    ]  # 189

    # job_required_main_skills_list = ['API design', 'Payments processing', 'Java']
    # job_required_main_skills_list = ['API design', 'Payments processing', 'Java']  # 3
    #
    # job_required_main_skills_list = ['API design', 'Payments processing', 'Blockchain']
    #
    # raw_filters = {'industry': ['Software Development'], 'job_function': ['engineering'],
    #                'job_title': ['Software Engineer'],
    #                'location': {'name': ['US']},
    #                'seniority': ['senior']}

    # job_required_main_skills_list = ['Golang', 'Python', 'Kubernetes', 'Distributed Systems', 'RESTful APIs',
    #                                  'Workflow Automations', 'Machine Learning',
    #                                  'Database Management (AWS Aurora, Cassandra, MySQL)'] #3

    # job_required_main_skills_list = ['Python', 'Kubernetes', 'Distributed Systems', 'RESTful APIs',]  # 2

    # job_required_main_skills_list = ['Python', 'Go']  # 3
    #
    # raw_filters = {'industry': ['Software Development'], 'job_function': ['information technology'],
    #  'job_title': ['Senior Backend Software Engineer'],
    #  'location': {'name': ['San Francisco', 'CA', 'USA']}, 'seniority': ['senior']}

    # # Single search
    # llm = DeepSeekWrapper()
    # search_results = main_search_linkedin(raw_filters, job_required_main_skills_list)
    # print("search_results:", search_results)

    # # Multi search
    # llm = DeepSeekWrapper()
    # llm = QwenWrapper()
    # llm = GeminiWrapper()

    MAX_SEARCH_NUM = 1000
    DOMAIN = "Software"

    llm = ChatGPTWrapper()
    linkedin_enum_params = get_linkedin_enum_data()
    linkedin_service = LinkedInService()

    jd_df = pd.read_csv(f"data/Jobs_Info_{DOMAIN}.csv")  # Jobs_Info_Software.csv
    output_list = []
    run_times = []
    for idx, row in jd_df.iterrows():
        print("idx:", idx)
        # if idx >= 10:
        #     break

        start_time = time.time()
        total_normal_input_tokens = 0
        total_normal_output_tokens = 0
        total_pro_input_tokens = 0
        total_pro_output_tokens = 0

        job_id = row["job_id"]
        print("job_id:", job_id)
        if int(job_id) not in [7934]:
            continue

        job_name = row["title"]
        # job_titles = row['job_title']

        job_desc = (
                str(row["title"])
                + "\n\n"
                + "Hiring company name: "
                + str(row["company_name"])
                + "\n"
                + str(row["location"])
                + "\n\n"
                + str(row["summary"])
                + "\n\n"
                + str(row["description"])
                + "\n\n"
                + str(row["requirements"])
                + "\n"
        )

        job_info, input_tokens, output_tokens = jd_basic_understanding_process(
            llm, job_desc
        )
        total_normal_input_tokens += input_tokens
        total_normal_output_tokens += output_tokens

        jd_understanding = ""
        raw_job_title = job_info.get("job_title")
        job_function = job_info.get("job_function")
        company_industry = job_info.get("company_industry")
        job_industry = job_info.get("candidate_job_industry")
        job_domain_field = job_info.get("job_domain_field")
        job_seniority = job_info.get("job_seniority")
        job_location = job_info.get("job_location")
        job_required_language = job_info.get("job_required_language")
        job_required_min_years = job_info.get("job_required_min_years")
        job_required_max_years = job_info.get("job_required_max_years")

        all_skills, mandatory_skills, good_to_have_skills, pro_input_tokens, pro_output_tokens = (
            job_skill_understanding_process(llm, job_desc)
        )
        total_pro_input_tokens += input_tokens
        total_pro_output_tokens += output_tokens
        print("1. jd_understanding run time", time.time() - start_time)

        job_location_list, input_tokens, output_tokens = convert_location_list(
            llm=llm, location_str=job_location
        )
        total_normal_input_tokens += input_tokens
        total_normal_output_tokens += output_tokens
        print("2. job location correction run time", time.time() - start_time)

        dedup_reranked_job_titles = raw_job_title
        format_filter_conditions = ""
        job_main_skills = []

        search_results_num = 0
        pick_num = np.inf
        retry_cnt = 0
        while search_results_num < 20 and retry_cnt < 3:
            job_required_main_skills_list = choose_job_main_skills(
                mandatory_skills, pick_num=pick_num
            )
            print("job_required_main_skills_list:", job_required_main_skills_list)
            raw_filters = {
                "job_function": [job_function],
                "seniority": [job_seniority],  ###
                "industry": [company_industry],
                "job_title": raw_job_title,
                "location": {
                    "name": job_location_list,
                },
                "year_of_experience": {"min": job_required_min_years, "max": job_required_max_years},
                "language": job_required_language

            }

            print("raw_filters:", raw_filters)
            (
                search_results,
                reranked_job_titles,
                format_filter_conditions,
                job_main_skills,
                input_tokens,
                output_tokens,
                sales_nav_filters,
            ) = main_linkedin_search_process(
                raw_filters, job_required_main_skills_list, llm
            )

            total_normal_input_tokens += input_tokens
            total_normal_output_tokens += output_tokens
            print("3. Search run time", time.time() - start_time)
            search_results_num = search_results["data"]["paging"]["total"]
            pick_num = len(job_required_main_skills_list)
            print("search_results_num:", search_results_num)
            retry_cnt += 1

        # # ToDo
        # job_main_skills ='"Microservices" OR "Blockchain Integration"'

        if search_results_num == 0:
            print(
                f"!!!!!!!!!!!!!!!!!!!!!!! Search result is 0 for job_id: {job_id} !!!!!!!!!!!!!!!!!!!!!!!!"
            )
            continue


        # Do a basic search with basic linkedin API to get basic info like linkedin_id and person summary
        batched_search_results = batch_basic_linkedin_search(
            search_results_num,
            format_filter_conditions,
            job_main_skills,
            max_search_num=MAX_SEARCH_NUM,
        )

        candidate_info_list = []
        for job_info in batched_search_results:
            (
                linkedin_id,
                first_name,
                last_name,
                degree,
                location,
                person_summary,
                current_position_summary,
                post_position_summary,
            ) = extract_candidate_info(job_info)
            candidate_info_list.append(
                [
                    job_id,
                    job_desc,
                    job_function,
                    company_industry,
                    job_industry,
                    job_seniority,
                    job_location,
                    job_required_language,
                    all_skills,
                    job_required_main_skills_list,
                    mandatory_skills,
                    linkedin_id,
                    first_name,
                    last_name,
                    degree,
                    location,
                    person_summary,
                    current_position_summary,
                    post_position_summary,
                ]
            )

        candidate_info_df = pd.DataFrame(
            candidate_info_list,
            columns=[
                "job_id",
                "job_desc",
                "job_function",
                "company_industry",
                "job_industry",
                "job_seniority",
                "job_location",
                "job_required_language",
                "all_skills",
                "job_required_main_skills_list",
                "mandatory_skills",
                "linkedin_id",
                "first_name",
                "last_name",
                "degree",
                "location",
                "person_summary",
                "current_position_summary",
                "post_position_summary",
            ],
        )

        # Use linkedin_id to search with improved linkedin api to get fixed experiences and educations
        linkedin_ids = candidate_info_df["linkedin_id"].tolist()
        raw_fixed_info_search_results, succeed, job_info = improved_linkedin_search_api(
            linkedin_ids, check_interval=1
        )
        fixed_candidate_info_df = extract_fixed_person_info(
            raw_fixed_info_search_results
        )

        candidate_info_df = candidate_info_df.merge(
            fixed_candidate_info_df, on="linkedin_id", how="left"
        )

        if len(candidate_info_df['linkedin_id'].unique()) != len(candidate_info_df):
            raise Exception(f"!!!!!!!!!!!!!!!!!!!!!!! Search linkedin_id is not unique for job_id: {job_id} !!!!!!!!!!!!!!!!!!!!!!!!")


        os.makedirs(f"data/{DOMAIN}_candidate_examples_{MAX_SEARCH_NUM}", exist_ok=True)
        candidate_info_df.to_csv(
            f"data/{DOMAIN}_candidate_examples_{MAX_SEARCH_NUM}/candidate_info_df_{job_id}.csv",
            index=False,
        )

        run_time = time.time() - start_time

        normal_cost = (
                total_normal_input_tokens * COST_NORMAL_INPUT_TOKEN
                + total_normal_output_tokens * COST_NORMAL_OUTPUT_TOKEN
        )
        pro_cost = (
                total_pro_input_tokens * COST_PRO_INPUT_TOKEN
                + total_pro_output_tokens * COST_PRO_OUTPUT_TOKEN
        )
        total_cost = normal_cost + pro_cost

        print(f"normal_cost: {normal_cost}, pro_cost: {pro_cost}, run_time: {run_time}")

        output_list.append(
            [
                job_id,
                search_results_num,
                job_name,
                job_desc,
                jd_understanding,
                job_function,
                company_industry,
                job_industry,
                job_seniority,
                raw_job_title,
                dedup_reranked_job_titles,
                job_location,
                job_location_list,
                job_required_language,
                job_required_main_skills_list,
                mandatory_skills,
                good_to_have_skills,
                run_time,
                total_normal_input_tokens,
                COST_NORMAL_INPUT_TOKEN,
                total_normal_input_tokens,
                COST_NORMAL_OUTPUT_TOKEN,
                normal_cost,
                total_pro_input_tokens,
                COST_PRO_INPUT_TOKEN,
                total_pro_output_tokens,
                COST_PRO_OUTPUT_TOKEN,
                pro_cost,
                total_cost,
            ]
        )

        print("=======================================================================")

    output_df = pd.DataFrame(
        output_list,
        columns=[
            "job_id",
            "search_results_num",
            "job_name",
            "job_desc",
            "jd_understanding",
            "job_function",
            "company_industry",
            "job_industry",
            "job_seniority",
            "raw_job_title",
            "dedup_reranked_job_titles",
            "job_location",
            "job_location_list",
            "job_required_language",
            "job_required_main_skills_list",
            "mandatory_skills",
            "good_to_have_skills",
            "run_time",
            "total_normal_input_tokens",
            "COST_NORMAL_INPUT_TOKEN",
            "total_normal_input_tokens",
            "COST_NORMAL_OUTPUT_TOKEN",
            "normal_cost",
            "total_pro_input_tokens",
            "COST_PRO_INPUT_TOKEN",
            "total_pro_output_tokens",
            "COST_PRO_OUTPUT_TOKEN",
            "pro_cost",
            "total_cost",
        ],
    )

    output_filepath = "output_df_deepseek_llm_v2.csv"
    output_df.to_csv("data/search_results_output.csv", index=False)

    # # Multiple search
    # jd_df = pd.read_csv("data/Jobs_Info_Software.csv")
    # for idx, row in jd_df.iterrows():
    #     job_id = row['job_id']
    #     job_title = row['job_title']
    #     job_desc = row['raw_job_description']
    #     jd_understanding = row['jd_understanding']
    #     job_function = row['job_function_y']
    #     job_industry = row['job_industry']
    #     job_seniority = row['job_seniority']
    #     job_location = row['job_location']
    #     job_required_language = row['job_required_language']
    #     job_required_main_skills = row['required_main_skills']
    #     job_title = ast.literal_eval(job_title)
    #     job_location_list = convert_location_list(location_str=job_location)
    #
    #     raw_filters = {
    #         "job_function": [job_function],
    #         "seniority": [job_seniority],
    #         "industry": [job_industry],
    #         "job_title": job_title,
    #         "location": {
    #             "name": job_location_list,
    #         }
    #     }
    #
    #     sales_nav_conditions = convert_filters_to_sales_nav_conditions(raw_filters, linkedin_service, linkedin_enum_params)
    #     linkedin_format_filter_conditions = multi_filters_to_str(sales_nav_conditions['filters'])
    #     print("linkedin_format_filter_conditions:", linkedin_format_filter_conditions)
    #     # linkedin_format_filter_conditions = """List((type:FUNCTION,values:List((id:8,text:Engineering,selectionType:INCLUDED),(id:19,text:Product%20Management,selectionType:INCLUDED))),(type:SENIORITY_LEVEL,values:List((id:220,text:Director,selectionType:INCLUDED),(id:310,text:CXO,selectionType:INCLUDED))),(type:INDUSTRY,values:List((id:3124,text:Internet%20News,selectionType:INCLUDED),(id:4,text:Software%20Development,selectionType:INCLUDED))),(type:CURRENT_TITLE,values:List((text:CTO,selectionType:INCLUDED),(text:VP%20Engineering,selectionType:INCLUDED))),(type:CURRENT_COMPANY,values:List((id:urn%3Ali%3Aorganization%3A1441,text:Google,selectionType:INCLUDED,parent:(id:0)),(id:urn%3Ali%3Aorganization%3A1035,text:Microsoft,selectionType:INCLUDED,parent:(id:0)))),(type:REGION,values:List((id:90000084,text:San%20Francisco%20Bay%20Area,selectionType:INCLUDED),(id:105080838,text:New%20York%2C%20United%20States,selectionType:INCLUDED))))"""
    #     search_results = search_linkedin(linkedin_format_filter_conditions)
    #     print("search_results:", search_results)
