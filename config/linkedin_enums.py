import logging
import os
import json
from functools import lru_cache

# from linkedin_apiservice.query_service import LinkedInQueryService
# from linkedin_apiservice.client import LinkedInClient
# from utils.logger import TaskLogger
from config.config import SEARCH_CHANNEL

# Base directory for LinkedIn enum data files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "linkedin_enum_data")

# 确保数据目录存在
os.makedirs(DATA_DIR, exist_ok=True)

# def initialize_linkedin_enum_data():
#     task_logger = TaskLogger()
#     task_logger.info('Project start: Initialize Linkedin Sales Nav enum value.')
#     task_logger.info('Created TaskLogger instance')
#
#     try:
#         client = LinkedInClient()
#         linkedin_query_service = LinkedInQueryService(client)
#         task_logger.info('Created LinkedIn client and query service')
#
#         # 获取并保存数据
#         task_logger.info('Fetching seniority data...')
#         seniority_data = linkedin_query_service.get_typeahead_suggestions("SENIORITY_V2")
#         with open(os.path.join(DATA_DIR, 'seniority.json'), 'w') as f:
#             json.dump(seniority_data, f)
#         task_logger.info('Saved seniority data')
#
#         task_logger.info('Fetching function data...')
#         jf_data = linkedin_query_service.get_typeahead_suggestions("FUNCTION")
#         with open(os.path.join(DATA_DIR, 'jf.json'), 'w') as f:
#             json.dump(jf_data, f)
#         task_logger.info('Saved function data')
#
#         task_logger.info('Fetching tenure data...')
#         tenure_data = linkedin_query_service.get_typeahead_suggestions("TENURE")
#         with open(os.path.join(DATA_DIR, 'tenure.json'), 'w') as f:
#             json.dump(tenure_data, f)
#         task_logger.info('Saved tenure data')
#
#         task_logger.info('Fetching industry data...')
#         industry_data = linkedin_query_service.get_typeahead_suggestions("INDUSTRY")
#         with open(os.path.join(DATA_DIR, 'industry_raw.json'), 'w') as f:
#             json.dump(industry_data, f)
#         task_logger.info('Saved industry data')
#
#         task_logger.info('LinkedIn enum data initialization completed successfully')
#
#     except Exception as e:
#         error_msg = f"Error during LinkedIn enum data initialization: {str(e)}"
#         task_logger.error(error_msg)
#         raise


# 从文件加载数据
def load_json_data(filename):
    file_path = os.path.join(DATA_DIR, filename)
    with open(file_path, "r") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def get_linkedin_enum_data():
    """
    Load LinkedIn enum data from JSON files.

    This function is cached to avoid repeated file I/O and JSON parsing.
    Data is loaded only once per process and reused for all subsequent calls.
    """
    # 加载所有枚举值
    try:
        task_logger = logging.getLogger("task_logger")
        task_logger.info("Loading enum data from files... (cached after first load)")

        seniority_data = load_json_data("seniority.json")
        seniority_list = [
            _.get("displayValue", "") for _ in seniority_data["elements"]
        ][::-1]
        task_logger.info(f"Loaded {len(seniority_list)} seniority values")

        jf_data = load_json_data("jf.json")
        jf_list = [_.get("displayValue", "") for _ in jf_data["elements"]]
        task_logger.info(f"Loaded {len(jf_list)} function values")

        tenure_data = load_json_data("tenure.json")
        tenure_list = [_.get("displayValue", "") for _ in tenure_data["elements"]]
        task_logger.info(f"Loaded {len(tenure_list)} tenure values")

        if SEARCH_CHANNEL== "recruiter":
            industry_data = load_json_data("industry_2.json")
        elif SEARCH_CHANNEL == "sales_nav":
            industry_data = load_json_data("industry_raw.json")
        else:
            raise ValueError("SEARCH_CHANNEL must be either 'recruiter' or 'sales_nav'")
        industry_list = [_[0] for _ in industry_data]
        task_logger.info(f"Loaded {len(industry_list)} industry values")

        # Degree data: Recruiter
        if SEARCH_CHANNEL== "recruiter":
            degree_data = load_json_data("degree.json")
        elif SEARCH_CHANNEL == "sales_nav":
            degree_data = load_json_data("degree.json")
        else:
            raise ValueError("SEARCH_CHANNEL must be either 'recruiter' or 'sales_nav'")

        # Language data: Recruiter
        # if SEARCH_CHANNEL== "recruiter":
        #     language_data = load_json_data("language.json")
        # elif SEARCH_CHANNEL == "sales_nav":
        language_data = load_json_data("language.json")
        # print('language_data: ', language_data)
        
        # else:
        #     raise ValueError("SEARCH_CHANNEL must be either 'recruiter' or 'sales_nav'")

        # # Salary data: Recruiter
        # salary_recruiter = load_json_data("salary_recruiter.json")

        # # Currency: Recruiter
        # currency_recruiter = load_json_data("currency_recruiter.json")

        area_range = [1, 5, 10, 25, 35, 50, 75, 100]
        task_logger.info("All enum data loaded successfully")
    except Exception as e:
        error_msg = f"Error loading enum data: {str(e)}"
        task_logger.error(error_msg)
        raise

    linkedin_enum_params = {
        "seniority_data": seniority_data,
        "jf_data": jf_data,
        "tenure_data": tenure_data,
        "industry_data": industry_data,
        "area_range": area_range,
        "degree_data": degree_data,
        "language_data": language_data,
        # "language_data": language_data,
        # "salary_recruiter": salary_recruiter,
        # "currency_recruiter": currency_recruiter,
    }

    return linkedin_enum_params







print(get_linkedin_enum_data())

# if __name__ == '__main__':
#     initialize_linkedin_enum_data()