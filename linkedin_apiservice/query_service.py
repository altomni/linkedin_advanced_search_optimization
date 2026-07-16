from typing import List, Dict
from linkedin_apiservice.client import LinkedInClient
from config.api_config import LinkedInConfig


class LinkedInQueryService:
    def __init__(self, client: LinkedInClient):
        self.client = client

    def search_with_filters(
        self, filters: str, keywords: str = "", logId: str = "", doLogHistory: str = ""
    ) -> Dict:
        """
        执行带过滤器的搜索
        """
        return self.client.post(
            LinkedInConfig.Endpoints.SALES_SEARCH,
            {
                "filters": filters,
                "keywords": keywords,
                "logId": logId,
                "doLogHistory": doLogHistory,
            },
        )

    def get_typeahead_suggestions(self, type_name: str) -> Dict:
        """
        获取特定类型的typeahead建议
        """
        return self.client.get(
            f"{LinkedInConfig.Endpoints.SALES_API_FACET}/{type_name}"
        )

    def search_typeahead(self, type_name: str, query: str) -> Dict:
        """
        搜索typeahead
        """
        params = {"typeName": type_name, "query": query}
        return self.client.get(LinkedInConfig.Endpoints.SALES_API_FACET_QUERY, params)

    def search_company(self, company_name=None, company_id=None) -> Dict:
        """
        搜索company具体信息
        """
        if company_name is None and company_id is None:
            raise ValueError(
                "search company: both company_name and company_id could not be None."
            )
        if company_name is not None:
            return self.client.get(
                LinkedInConfig.Endpoints.GET_COMPANY_INFO_BY_NAME,
                value=company_name,
                params={},
            )
        elif company_id is not None:
            return self.client.get(
                LinkedInConfig.Endpoints.GET_COMPANY_INFO_BY_ID,
                value=company_id,
                params={},
            )


if __name__ == "__main__":
    # 直接运行时的代码
    pass
