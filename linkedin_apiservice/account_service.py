from typing import List, Dict
from linkedin_apiservice.client import LinkedInClient
from config.api_config import LinkedInConfig


class LinkedInAccountService:
    def __init__(self, client: LinkedInClient):
        self.client = client

    def create_list(self, list_name: str) -> Dict:
        """
        创建新列表
        """
        return self.client.post(
            LinkedInConfig.Endpoints.LISTS_CREATE, {"listName": list_name}
        )

    def add_companies_to_list(self, list_id: str, company_ids: List[int]) -> Dict:
        """
        添加公司到列表
        """
        return self.client.post(
            LinkedInConfig.Endpoints.LISTS_ADD_COMPANIES,
            {"listId": list_id, "companyIds": company_ids},
        )
