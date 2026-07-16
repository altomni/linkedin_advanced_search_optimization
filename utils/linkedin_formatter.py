# from linkedin_apiservice.management_service import LinkedInService

import re
from urllib.parse import quote

from config.linkedin_enums import get_linkedin_enum_data
from linkedin_apiservice.management_service import LinkedInService
from jd_understanding_funcs import DeepSeekWrapper, job_title_adjustment


def remove_special_characters(text):
    return re.sub(r"[^a-zA-Z0-9\s]", "", text)


def convert_filters_to_sales_nav_conditions(
    filters, linkedin_service, linkedin_enum_params, llm
):
    """Convert filter dictionary to LinkedIn Sales Navigator search conditions

    Args:
        filters (dict): Dictionary containing filter parameters like experience, industry, job title etc.
        linkedin_service: LinkedIn service instance for making API calls

    Returns:
        dict: Formatted conditions for LinkedIn Sales Navigator search
    """
    # Function conditions
    function_condition = {"type": "FUNCTION", "values": []}
    for jf in filters.get("job_function", []):
        match = [
            _["id"]
            for _ in linkedin_enum_params["jf_data"]["elements"]
            if _["displayValue"] == jf
        ]
        if len(match) > 0:
            value = {"id": f"{match[0]}", "text": jf, "selectionType": "INCLUDED"}
            function_condition["values"].append(value)

    # Seniority conditions
    seniority_condition = {"type": "SENIORITY_LEVEL", "values": []}
    for level in filters.get("seniority", []):
        match = [
            _["id"]
            for _ in linkedin_enum_params["seniority_data"]["elements"]
            if _["displayValue"] == level
        ]
        if len(match) > 0:
            value = {"id": f"{match[0]}", "text": level, "selectionType": "INCLUDED"}
            seniority_condition["values"].append(value)

    # Experience year conditions
    exp_year_condition = {"type": "YEARS_OF_EXPERIENCE", "values": []}
    for exp_year in filters.get("experience_year", []):
        match = [
            _["id"]
            for _ in linkedin_enum_params["tenure_data"]["elements"]
            if _["displayValue"] == exp_year
        ]
        if len(match) > 0:
            value = {"id": f"{match[0]}", "text": exp_year, "selectionType": "INCLUDED"}
            exp_year_condition["values"].append(value)

    # Industry conditions
    ind_condition = {"type": "INDUSTRY", "values": []}
    for ind in filters.get("industry", []):
        match = [
            _["id"]
            for _ in linkedin_enum_params["industry_data"]["elements"]
            if _["displayValue"] == ind
        ]
        if len(match) > 0:
            value = {"id": f"{match[0]}", "text": ind, "selectionType": "INCLUDED"}
            ind_condition["values"].append(value)

    # Title conditions
    current_title_condition = {"type": "CURRENT_TITLE", "values": []}
    inner_input_tokens = 0
    inner_output_tokens = 0
    for title in filters.get("job_title", []):
        if not title:
            continue
        formatted_title = remove_special_characters(title)

        data = {"elements": []}
        data = linkedin_service.query_service.search_typeahead("TITLE", quote(formatted_title))
        print(f"job_title: {formatted_title}, data: {data}")

        # Adjust most likely job title
        if len(data["elements"]) == 0:
            adjust_formatted_title, input_tokens, output_tokens = job_title_adjustment(
                llm, formatted_title
            )
            print(
                f"formatted_title: {formatted_title}, adjust_formatted_title: {adjust_formatted_title}"
            )
            inner_input_tokens += input_tokens
            inner_output_tokens += output_tokens
            try:
                data = linkedin_service.query_service.search_typeahead(
                    "TITLE", quote(adjust_formatted_title)
                )
                print(f"adjust_formatted_title: {adjust_formatted_title}, data: {data}")
            except Exception as e:
                print(f"Fetch TITLE error at {e}")

        candidates = [
            {"displayValue": record["displayValue"], "id": record["id"]}
            for record in data["elements"]
        ]
        if len(candidates) == 0:
            print("data:", data)
        # data = {"elements": reranked_candidates}

        # try:
        #     title_id = data['elements'][0]['id']
        #     title_name = data['elements'][0]['displayValue']
        #     value = {
        #         "id": f"{title_id}",
        #         "text": title_name,
        #         "selectionType": "INCLUDED",
        #         "parent": {"id": "0"}
        #     }
        # except:
        #     value = {
        #         "text": formatted_title,
        #         "selectionType": "INCLUDED",
        #         "parent": {"id": "0"}
        #     }

        for candidate in candidates:
            try:
                title_id = candidate["id"]
                title_name = candidate["displayValue"]
                value = {
                    "id": f"{title_id}",
                    "text": title_name,
                    "selectionType": "INCLUDED",
                    "parent": {"id": "0"},
                }
            except:
                value = {
                    "text": formatted_title,
                    "selectionType": "INCLUDED",
                    "parent": {"id": "0"},
                }
            current_title_condition["values"].append(value)

    # Location conditions
    if "zipcode" in filters.get("location", {}).keys():
        geo_location_condition = {
            "type": "POSTAL_CODE",
            "values": [],
            "selectedSubFilter": f"{str(linkedin_enum_params['area_range'][-1])}",
        }
        for zipcode in filters["location"]["zipcode"]:
            if not zipcode:
                continue
            data = linkedin_service.query_service.search_typeahead(
                "BING_GEO_POSTAL_CODE", quote(zipcode)
            )
            zip_code_id = data["elements"][0]["id"]
            zip_code_area_name = data["elements"][0]["displayValue"]
            value = {
                "id": zip_code_id,
                "text": zip_code_area_name,
                "selectionType": "INCLUDED",
            }
            geo_location_condition["values"].append(value)
    else:
        geo_location_condition = {"type": "REGION", "values": []}
        geo_name_list = filters.get("location", {}).get("name", [])
        for geo in geo_name_list:
            print("geo: ", geo)
            formatted_geo = remove_special_characters(geo)
            data = linkedin_service.query_service.search_typeahead(
                "BING_GEO", quote(formatted_geo)
            )
            try:
                geo_id = data["elements"][0]["id"]
            except:
                continue
            geo_name = data["elements"][0]["displayValue"]
            value = {"id": geo_id, "text": geo_name, "selectionType": "INCLUDED"}
            geo_location_condition["values"].append(value)

    # Company conditions
    current_company_condition = {"type": "CURRENT_COMPANY", "values": []}
    # if len(filters.get('companies', [])) <= 50:
    for company_data in filters.get("companies", []):
        if not company_data:
            continue
        company = company_data["company_name"]
        id_ = company_data.get("company_id", "")
        formatted_company = remove_special_characters(company)

        # if not id_:
        #     value = {
        #         "text": formatted_company,
        #         "selectionType": "INCLUDED",
        #         "parent": {"id": "0"}
        #     }
        #     current_company_condition["values"].append(value)
        #     continue

        try:
            data = linkedin_service.query_service.search_typeahead(
                "COMPANY", quote(formatted_company)
            )
            find = 0
            for result in data["elements"]:
                company_id = result["id"]
                company_name = result["displayValue"]
                # if int(company_id) == int(id_):
                value = {
                    "id": f"{company_id}",
                    "text": company_name,
                    "selectionType": "INCLUDED",
                    "parent": {"id": "0"},
                }
                current_company_condition["values"].append(value)
                find += 1
                break

            ## 如果轮循完所有结果也没找到
            if find == 0:
                value = {
                    "text": formatted_company,
                    "selectionType": "INCLUDED",
                    "parent": {"id": "0"},
                }
                current_company_condition["values"].append(value)
        except Exception as e:
            print(f"company search error: {e}")
            value = {
                "text": formatted_company,
                "selectionType": "INCLUDED",
                "parent": {"id": "0"},
            }
            current_company_condition["values"].append(value)

    # Combine all conditions
    conditions = []
    for condition in [
        function_condition,
        seniority_condition,
        exp_year_condition,
        ind_condition,
        current_title_condition,
        geo_location_condition,
        current_company_condition,
    ]:
        if condition.get("values"):
            conditions.append(condition)

    return {
        "keywords": filters.get("keywords", ""),
        "filters": conditions,
        "input_tokens": inner_input_tokens,
        "output_tokens": inner_output_tokens,
    }


# Export the functions
__all__ = ["convert_filters_to_sales_nav_conditions", "remove_special_characters"]


if __name__ == "__main__":
    # 构建搜索条件
    filters = {
        "job_function": ["Engineering", "Product Management"],
        "seniority": ["Director", "CXO"],
        "industry": ["Internet", "Software"],
        "job_title": ["CTO", "VP Engineering"],
        "companies": [{"company_name": "Apple"}, {"company_name": "MSI"}],
        "location": {"name": ["San Francisco Bay Area", "New York"]},
    }

    llm = DeepSeekWrapper()

    linkedin_enum_params = get_linkedin_enum_data()
    linkedin_service = LinkedInService()
    result = convert_filters_to_sales_nav_conditions(
        filters, linkedin_service, linkedin_enum_params, llm
    )

    # 输出结果
    print(result)
