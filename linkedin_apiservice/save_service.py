from typing import Dict
from linkedin_apiservice.client import LinkedInClient
from config.api_config import LinkedInConfig


class LinkedInSaveService:
    def __init__(self, client: LinkedInClient):
        self.client = client

    def save_search(self, filters: str, session_id: str, log_id: str) -> Dict:
        """
        保存搜索结果
        """
        return self.client.post(
            LinkedInConfig.Endpoints.SEARCH_SAVE,
            {
                "filters": filters,
                "sessionId": session_id,
                "logId": log_id,
                "doLogHistory": True,
            },
        )

    def download_data(self, download_id: str) -> Dict:
        """
        下载数据
        """
        return self.client.get(f"{LinkedInConfig.Endpoints.DOWNLOAD}/{download_id}")
