from typing import Dict, List, Any
import json
from urllib.parse import quote, unquote


class QueryComposeService:
    def __init__(self):
        self.type_handlers = {
            # Company
            "CURRENT_COMPANY": self._handle_company_filter,
            "COMPANY_HEADCOUNT": self._handle_id_text_filter,
            "PAST_COMPANY": self._handle_company_filter,
            "COMPANY_TYPE": self._handle_id_text_filter,
            "COMPANY_HEADQUARTERS": self._handle_id_text_filter,
            "YEARS_AT_CURRENT_COMPANY": self._handle_id_text_filter,
            "YEARS_IN_CURRENT_POSITION": self._handle_id_text_filter,
            # Role
            "FUNCTION": self._handle_id_text_filter,
            "CURRENT_TITLE": self._handle_mixed_filter,
            "SENIORITY_LEVEL": self._handle_id_text_filter,
            "PAST_TITLE": self._handle_mixed_filter,
            # Personal
            "POSTAL_CODE": self._handle_id_text_selectedSubFilter_filter,
            "REGION": self._handle_id_text_filter,
            "INDUSTRY": self._handle_id_text_filter,
            "FIRST_NAME": self._handle_text_filter,
            "LAST_NAME": self._handle_text_filter,
            "PROFILE_LANGUAGE": self._handle_id_text_filter,
            "YEARS_OF_EXPERIENCE": self._handle_id_text_filter,
            "GROUP": self._handle_id_text_filter,
            "SCHOOL": self._handle_id_text_filter,
            # Others
            "RELATIONSHIP": self._handle_id_text_filter,
            "CONNECTION_OF": self._handle_id_text_filter,
            "ACCOUNT_LIST": self._handle_id_text_filter,
            "LEAD_LIST": self._handle_id_text_filter,
            "LEAD_INTERACTIONS": self._handle_id_text_filter,
            "SAVED_LEADS_AND_ACCOUNTS": self._handle_id_text_filter,
            "PERSONA": self._handle_id_text_filter,
            "FOLLOWS_YOUR_COMPANY": self._handle_id_text_filter,
            "PAST_COLLEAGUE": self._handle_id_only_filter,
        }

    def compose_query(self, filters_json: Dict) -> str:
        """
        将JSON格式的过滤器转换为LinkedIn查询字符串
        """
        try:
            filters = filters_json.get("filters", [])
            filter_parts = []

            for filter_item in filters:
                filter_type = filter_item.get("type")
                handler = self.type_handlers.get(filter_type)

                if handler:
                    filter_part = handler(filter_item)
                    if filter_part:
                        filter_parts.append(filter_part)

            if not filter_parts:
                return ""

            complete_query = f"List({','.join(filter_parts)})"
            return complete_query

        except Exception as e:
            print(f"Error composing query: {str(e)}")
            return ""

    def _handle_company_filter(self, filter_item: Dict) -> str:
        """处理公司相关的过滤器"""
        values = filter_item.get("values", [])
        value_parts = []

        for value in values:
            # 按固定顺序构建parts
            parts = []
            # 1. id 总是第一个
            if "id" in value:
                parts.append(f"id:{quote(value['id'])}")
            # 2. text 总是第二个
            if "text" in value:
                parts.append(f"text:{quote(value['text'])}")
            # 3. selectionType 总是第三个
            if "selectionType" in value:
                parts.append(f"selectionType:{value['selectionType']}")
            # 4. parent 总是第四个
            if "parent" in value:
                parent_id = value["parent"].get("id")
                parts.append(f"parent:(id:{parent_id})")
            # 5. icon 如果存在总是最后
            if "id" in value and "icon" in value and value["icon"] == "list":
                parts.append("icon:list")

            value_parts.append(f"({','.join(parts)})")

        return f"(type:{filter_item['type']},values:List({','.join(value_parts)}))"

    def _handle_id_text_filter(self, filter_item: Dict) -> str:
        """处理同时包含ID和文本的过滤器"""
        values = filter_item.get("values", [])
        value_parts = []

        for value in values:
            parts = []
            if "id" in value:
                parts.append(f"id:{value['id']}")
            if "text" in value:
                parts.append(f"text:{quote(value['text'])}")
            parts.append(f"selectionType:{value['selectionType']}")

            value_parts.append(f"({','.join(parts)})")

        return f"(type:{filter_item['type']},values:List({','.join(value_parts)}))"

    def _handle_id_text_selectedSubFilter_filter(self, filter_item: Dict) -> str:
        """处理同时包含ID、文本和selectedSubFilter的过滤器"""
        # 处理values部分
        values = filter_item.get("values", [])
        value_parts = []

        for value in values:
            parts = []
            if "id" in value:
                parts.append(f"id:{value['id']}")
            if "text" in value:
                parts.append(f"text:{quote(value['text'])}")
            parts.append(f"selectionType:{value['selectionType']}")

            value_parts.append(f"({','.join(parts)})")

        # 构建基本结构
        parts = [f"type:{filter_item['type']}", f"values:List({','.join(value_parts)})"]

        # 添加selectedSubFilter如果存在
        if "selectedSubFilter" in filter_item:
            parts.append(f"selectedSubFilter:{filter_item['selectedSubFilter']}")

        return f"({','.join(parts)})"

    def _handle_text_filter(self, filter_item: Dict) -> str:
        """处理仅包含文本的过滤器"""
        values = filter_item.get("values", [])
        value_parts = []

        for value in values:
            parts = [f"text:{value['text']}", f"selectionType:{value['selectionType']}"]
            value_parts.append(f"({','.join(parts)})")

        return f"(type:{filter_item['type']},values:List({','.join(value_parts)}))"

    def _handle_id_only_filter(self, filter_item: Dict) -> str:
        """处理仅包含ID的过滤器"""
        values = filter_item.get("values", [])
        value_parts = []

        for value in values:
            parts = [f"id:{value['id']}", f"selectionType:{value['selectionType']}"]
            value_parts.append(f"({','.join(parts)})")

        return f"(type:{filter_item['type']},values:List({','.join(value_parts)}))"

    def _handle_mixed_filter(self, filter_item: Dict) -> str:
        """处理混合类型的过滤器（可能同时包含ID和文本，或仅包含其中之一）"""
        values = filter_item.get("values", [])
        value_parts = []

        for value in values:
            parts = []
            if "id" in value:
                parts.append(f"id:{value['id']}")
            if "text" in value:
                parts.append(f"text:{quote(value['text'])}")
            parts.append(f"selectionType:{value['selectionType']}")

            value_parts.append(f"({','.join(parts)})")

        return f"(type:{filter_item['type']},values:List({','.join(value_parts)}))"

    def compose_full_url(
        self,
        filters_json: Dict,
        session_id: str,
        search_id: str,
        log_option: str = "false",
        view_option: str = "true",
    ) -> str:
        """
        生成完整的LinkedIn搜索URL

        Args:
            filters_json: 过滤器JSON数据
            session_id: 会话ID
            search_id: 搜索ID，默认"4176449322"
            log_option: 日志选项，默认"true"
            view_option: 视图选项，默认"true"
        """
        filters_str = self.compose_query(filters_json)
        keywords = filters_json.get("keywords", "")

        url = "https://www.linkedin.com/sales/search/people?query=("
        if keywords:
            url += "spellCorrectionEnabled%3Atrue%2C"
        url += (
            "recentSearchParam%3A(id%3A"
            + search_id
            + "%2CdoLogHistory%3A"
            + log_option
            + ")"
        )
        # 有条件地添加filters
        if filters_str:
            url += "%2Cfilters%3A" + quote(filters_str)
        # 有条件地添加keywords
        if keywords:
            url += "%2Ckeywords%3A" + quote(quote(keywords))
        # 完成URL
        url += ")&sessionId=" + quote(session_id) + "&viewAllFilters=" + view_option
        return url
