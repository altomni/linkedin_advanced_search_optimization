import logging
import uuid
import sys
import os

import requests
from typing import Dict, Any, Optional

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
project_root = os.path.dirname(src_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from config.api_config import LinkedInConfig


class LinkedInClient:
    def __init__(self):
        self.base_url = LinkedInConfig.BASE_URL
        self.task_id = str(uuid.uuid4())
        self.logger = logging.getLogger(__name__)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "en-US",
        }

    def get(
        self, endpoint: str, params: Optional[Dict] = None, value: Optional[Dict] = None
    ) -> Dict:
        if value:
            url = f"{self.base_url}{endpoint}/{value}"
        else:
            url = f"{self.base_url}{endpoint}"
        try:
            response = requests.get(url, headers=self._get_headers(), params=params)
            if response.status_code != 200:
                self.logger.error(
                    f"HTTP error! status: {response.status_code}", exc_info=True
                )
                raise Exception(f"HTTP error! status: {response.status_code}")
            return response.json()
        except Exception as e:
            self.logger.error(f"GET request failed: {e}", exc_info=True)
            raise

    def post(self, endpoint: str, data: Dict) -> Dict:
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.post(url, headers=self._get_headers(), json=data)
            if response.status_code != 200:
                self.logger.error(
                    f"HTTP error! status: {response.status_code}", exc_info=True
                )
                raise Exception(f"HTTP error! status: {response.status_code}")
            return response.json()
        except Exception as e:
            self.logger.error(f"POST request failed: {e}", exc_info=True)
            raise


# import uuid
# import logging
#
# import requests
# from typing import Dict, Any, Optional
# import sys
# import os
#
# # 获取当前文件所在目录的上一层目录
# current_dir = os.path.dirname(os.path.abspath(__file__))
# parent_dir = os.path.dirname(current_dir)
#
# # 将上层目录添加到 Python 路径中
# if parent_dir not in sys.path:
#     sys.path.insert(0, parent_dir)
#
# # 现在可以直接导入
# from config.api_config import LinkedInConfig
# # from utils.context import task_ctx
# # from utils.logger import TaskLogger
#
#
# class LinkedInClient:
#     def __init__(self):
#         self.base_url = LinkedInConfig.BASE_URL
#         self.task_id = str(uuid.uuid4())
#         self.logger = logging.getLogger(__name__)
#
#     def _get_headers(self) -> Dict[str, str]:
#         return {
#             "Content-Type": "application/json",
#             "Accept": "*/*",
#             "Accept-Language": "en-US"
#         }
#
#     def get(self, endpoint: str, params: Optional[Dict] = None, value: Optional[Dict] = None) -> Dict:
#         if value:
#             url = f"{self.base_url}{endpoint}/{value}"
#         else:
#             url = f"{self.base_url}{endpoint}"
#         try:
#             response = requests.get(url, headers=self._get_headers(), params=params)
#             if response.status_code != 200:
#                 self.logger.error(f"HTTP error! status: {response.status_code}", exc_info=True)
#                 raise Exception(f"HTTP error! status: {response.status_code}")
#             return response.json()
#         except Exception as e:
#             self.logger.error(f"GET request failed: {e}", exc_info=True)
#             raise
#
#     def post(self, endpoint: str, data: Dict) -> Dict:
#         url = f"{self.base_url}{endpoint}"
#         try:
#             response = requests.post(url, headers=self._get_headers(), json=data)
#             if response.status_code != 200:
#                 self.logger.error(f"HTTP error! status: {response.status_code}", exc_info=True)
#                 raise Exception(f"HTTP error! status: {response.status_code}")
#             return response.json()
#         except Exception as e:
#             self.logger.error(f"POST request failed: {e}", exc_info=True)
#             raise


if __name__ == "__main__":
    import json

    client = LinkedInClient()
    x = client.get(
        LinkedInConfig.Endpoints.SALES_API_FACET_QUERY,
        {"typeName": "TITLE", "query": "engineer"},
    )
    print(json.dumps(x, indent=2, ensure_ascii=False))

# #【Query】
# # 帮助获取返回数量及其他参数
# curl -X POST "http://localhost:3005/api/linkedin/sales/search" -H "Content-Type: application/json" -d "{\"filters\":\"List((type:COMPANY_HEADCOUNT,values:List((id:D,text:51-200,selectionType:INCLUDED))),(type:COMPANY_TYPE,values:List((id:D,text:Educational%20Institution,selectionType:INCLUDED))),(type:SENIORITY_LEVEL,values:List((id:110,text:Entry%20Level,selectionType:INCLUDED),(id:200,text:Entry%20Level%20Manager,selectionType:INCLUDED))))\"}"

# #帮助获取相关搜索
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/COMPANY_WITH_LIST
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/COMPANY_SIZE
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/COMPANY_TYPE
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/BING_GEO
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/FUNCTION
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/TITLE
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/SENIORITY_V2
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/TENURE
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/INDUSTRY
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/PROFILE_LANGUAGE
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/GROUP
# http://localhost:3005/api/linkedin/salesApiFacetTypeahead/SCHOOL

# #搜索API
# http://localhost:3005/api/linkedin/salesApiFacetTypeaheadQuery?typeName=TITLE&query=title


# #【Account】
# #创建Account
# curl -X POST "http://localhost:3005/api/linkedin/lists/create" ^ -H "Content-Type: application/json" ^ -H "accept: */*" ^ -H "accept-language: en-US" ^ -d "{\"listName\":\"test_list_name3\"}"

# #导入数据
# curl -X POST "http://localhost:3005/api/linkedin/lists/add-companies" ^
#   -H "Content-Type: application/json" ^
#   -H "accept: */*" ^
#   -H "accept-language: en-US" ^
#   -d "{\"listId\":\"7297806872919638016\",\"companyIds\":[67741039,92963817,10704014]}"


# #【Save List】
# # 保存数据
# curl -X POST "http://localhost:3005/api/linkedin/search/save" ^
#   -H "Content-Type: application/json" ^
#   -H "accept: */*" ^
#   -H "accept-language: en-US" ^
#   -d "{\"filters\":\"List((type:REGION,values:List((id:91000006,text:DACH,selectionType:INCLUDED))),(type:FUNCTION,values:List((id:25,text:Sales,selectionType:INCLUDED))))\",\"sessionId\":\"AwyTju/4RkG4RfNHLe6JDw==\",\"logId\":\"4168767098\",\"doLogHistory\":true}"

# # 下载数据
# http://localhost:3005/api/linkedin/download/1891751202
