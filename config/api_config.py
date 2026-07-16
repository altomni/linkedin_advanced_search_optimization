import os
from dotenv import load_dotenv

# 加载.env文件中的环境变量
load_dotenv()


class LinkedInConfig:
    BASE_URL = os.getenv(
        "LINKEDIN_SERVICE_BASE_URL", "http://71.11.104.111:3456/api/linkedin"
    )  # "http://192.168.1.113:3000/api/linkedin"

    RECRUITER_API_BASE_URL = os.getenv("SYNONYM_ASSOCIATION_API_BASE_URL", "")

    class Endpoints:
        SALES_SEARCH = os.getenv("LINKEDIN_SALES_SEARCH_END_POINT", "/sales/search")
        SALES_API_FACET = os.getenv(
            "LINKEDIN_SALES_API_FACET_END_POINT", "/salesApiFacetTypeahead"
        )
        SALES_API_FACET_QUERY = os.getenv(
            "LINKEDIN_SALES_API_FACET_QUERY_END_POINT", "/salesApiFacetTypeaheadQuery"
        )
        LISTS_CREATE = os.getenv("LINKEDIN_LISTS_CREATE_END_POINT", "/lists/create")
        LISTS_ADD_COMPANIES = os.getenv(
            "LINKEDIN_LISTS_ADD_COMPANIES_END_POINT", "/lists/add-companies"
        )
        SEARCH_SAVE = os.getenv("LINKEDIN_SEARCH_SAVE_END_POINT", "/search/save")
        DOWNLOAD = os.getenv("LINKEDIN_DOWNLOAD_END_POINT", "/download")
        GET_COMPANY_INFO_BY_NAME = os.getenv(
            "LINKEDIN_GET_COMPANY_INFO_BY_NAME", "/companyProfileByName"
        )
        GET_COMPANY_INFO_BY_ID = os.getenv(
            "LINKEDIN_GET_COMPANY_INFO_BY_ID", "/companyProfileById"
        )
