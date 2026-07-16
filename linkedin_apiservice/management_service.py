from linkedin_apiservice.client import LinkedInClient
from linkedin_apiservice.query_service import LinkedInQueryService
from linkedin_apiservice.account_service import LinkedInAccountService
from linkedin_apiservice.save_service import LinkedInSaveService
from linkedin_apiservice.formatter import LinkedInDataFormatter
from linkedin_apiservice.query_composer import QueryComposeService


class LinkedInService:
    def __init__(self):
        self.client = LinkedInClient()
        self.query_service = LinkedInQueryService(self.client)
        self.account_service = LinkedInAccountService(self.client)
        self.save_service = LinkedInSaveService(self.client)
        self.query_compose_service = QueryComposeService()
        self.formatter = LinkedInDataFormatter()

    def filter_query_search(self, data):
        print("data: ", data)
        filter_query = self.query_compose_service.compose_query(data)
        print("search filters: ")
        print(filter_query)
        return self.query_service.search_with_filters(
            filter_query, data.get("keywords", "")
        )

    def company_search(self, company_name=None, company_id=None):
        return self.query_service.search_company(
            company_name=company_name, company_id=company_id
        )


if __name__ == "__main__":

    # Start
    linkedin_service = LinkedInService()

    # a = linkedin_service.filter_query_search(data = {'keywords': '', 'filters': [{'type': 'CURRENT_TITLE', 'values': [{'id': '42', 'text': 'Business Development Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '12621', 'text': 'Enterprise Sales Executive', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '20', 'text': 'Account Executive', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '9625', 'text': 'Sales Development Representative', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '2115', 'text': 'Business Development Associate', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '42', 'text': 'Business Development Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '118', 'text': 'Sales Executive', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '20', 'text': 'Account Executive', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '535', 'text': 'Salesperson', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '98', 'text': 'Business Development Specialist', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '526', 'text': 'Senior Sales Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '1609', 'text': 'Technical Sales Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '2286', 'text': 'Solutions Sales Specialist', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '119', 'text': 'Director of Business Development', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '5570', 'text': 'Enterprise Sales Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '42', 'text': 'Business Development Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '14', 'text': 'Sales Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '11', 'text': 'Account Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '535', 'text': 'Salesperson', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '98', 'text': 'Business Development Specialist', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '753', 'text': 'Senior Sales Representative', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '4781', 'text': 'Solutions Sales Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '19916', 'text': 'Client Development Executive', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '3715', 'text': 'Enterprise Sales Specialist', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '5544', 'text': 'Business Development Lead', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '1221', 'text': 'Technical Sales Representative', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '4087', 'text': 'Global Sales Director', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '5656', 'text': 'Customer Solutions Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '4180', 'text': 'Strategic Account Executive', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Sales Operations Lead', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '14', 'text': 'Sales Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '42', 'text': 'Business Development Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '11', 'text': 'Account Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '535', 'text': 'Salesperson', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '98', 'text': 'Business Development Specialist', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '753', 'text': 'Senior Sales Representative', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '4781', 'text': 'Solutions Sales Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '493', 'text': 'Client Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '920', 'text': 'Sales Operations Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '434', 'text': 'Business Development Executive', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Global Sales Associate', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '16363', 'text': 'Customer Success Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Sales Leadership Consultant', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '19941', 'text': 'Enterprise Solutions Specialist', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Strategic Sales Lead', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '3092', 'text': 'Director of Sales And Business Development', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '4535', 'text': 'Technical Sales Consultant', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': '705', 'text': 'Global Account Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Business Expansion Manager', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Sales Strategy Lead', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}]}, {'type': 'REGION', 'values': [{'id': '104119503', 'text': 'Sunnyvale, California, United States', 'selectionType': 'INCLUDED'}]}, {'type': 'CURRENT_COMPANY', 'values': [{'id': 'urn:li:organization:88007673', 'text': 'Perplexity', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Perplexity', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'MosaicML', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:3477522', 'text': 'Databricks', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Databricks', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Togetherai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:18423065', 'text': 'RUNPOD', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'RunPod', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:79045818', 'text': 'Modal', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Modal', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:2962005', 'text': 'anySCALE Architecture Design', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Anyscale', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Replica', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Huggingface', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'WeightsBias', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Scale AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:11130470', 'text': 'OpenAI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'OpenAI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:24024765', 'text': 'Cohere', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Cohere', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:10858000', 'text': 'Cerebras Systems', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Cerebras Systems', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:18464083', 'text': 'SambaNova Systems', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'SambaNova', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:229433', 'text': 'Cloudera', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Cloudera', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:104648263', 'text': 'snowflake', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Snowflake', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:2672915', 'text': 'DataRobot', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'DataRobot', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'H2Oai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:1076967', 'text': 'Algorithmia', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Algorithmia', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:1594050', 'text': 'Google DeepMind', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'DeepMind', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Inflection AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Lightning AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:18179511', 'text': 'Determined AI, acquired by Hewlett Packard Enterprise company in 2021', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Determined AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Paperspace', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Lambda Labs', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:36121341', 'text': 'CoreWeave', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'CoreWeave', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Vastai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:73802019', 'text': 'Baseten', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Baseten', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:15250931', 'text': 'Valohai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Valohai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Cometml', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Neptuneai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'OctoML', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:10812092', 'text': 'Graphcore', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Graphcore', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Cnvrgio', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Domino Data Lab', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:2770554', 'text': 'Dataiku', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Dataiku', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Tecton', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:11167639', 'text': 'Featureform', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Featureform', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:68302528', 'text': 'Snorkel AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Snorkel AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:18526564', 'text': 'Labelbox', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Labelbox', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Arize AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:18913526', 'text': 'Fiddler AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Fiddler AI', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:11533639', 'text': 'Roboflow', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Roboflow', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:10064814', 'text': 'Clarifai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Clarifai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:903031', 'text': 'Alteryx', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Alteryx', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:492926', 'text': 'Altair RapidMiner', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'RapidMiner', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Seldon', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'C3ai', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'id': 'urn:li:organization:20708', 'text': 'Palantir Technologies', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}, {'text': 'Palantir', 'selectionType': 'INCLUDED', 'parent': {'id': '0'}}]}]})

    # company_result = linkedin_service.company_search(company_name='google')
    # print(company_result)

    # # 【Step 1: Format】
    # rerank_benchmark_data={
    #     "keywords":'("project2 manager" OR "program manager") AND (startup OR tech) AND (NOT intern) AND (engineering OR software)',
    #     "filters":[
    #         {
    #             "type": "CURRENT_COMPANY",
    #             "values": [
    #                 {
    #                     "id": "urn:li:organization:2192",
    #                     "text": "Chevron",
    #                     "selectionType": "INCLUDED",
    #                     "parent": {
    #                         "id": "0"
    #                     }
    #                 }
    #             ]
    #         }
    #     ]}
    # filter_query = linkedin_service.query_compose_service.compose_query(rerank_benchmark_data)
    #
    # # 【Step 2: Test API CALL】
    # # 测试 1: 搜索过滤器
    # print("测试搜索过滤器...")
    # filters = "List((type:CURRENT_COMPANY,values:List((id:urn%3Ali%3Aorganization%3A2192,text:Chevron,selectionType:INCLUDED,parent:(id:0)))))"
    # keywords = '("project2 manager" OR "program manager") AND (startup OR tech) AND (NOT intern) AND (engineering OR software)'
    # results = linkedin_service.query_service.search_with_filters(filters, keywords)
    # print("搜索结果:", results)
    # print("-" * 50)

    # # # 测试 2: Typeahead Query 搜索
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.search_typeahead("TITLE", "Software")
    # print("搜索建议:", suggestions)
    # print("-" * 50)
    #
    # # # 测试 3: Typeahead Static 搜索
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.get_typeahead_suggestions("SENIORITY_V2")
    # print("SENIORITY_V2搜索建议:", suggestions)
    # print("-" * 50)
    #
    # # # 测试 4: Typeahead Static 搜索
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.get_typeahead_suggestions("Function")
    # print("Function搜索建议:", suggestions)
    # print("-" * 50)
    #
    # # # 测试 5: Typeahead Static 搜索
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.get_typeahead_suggestions("TENURE")
    # print("TENURE搜索建议:", suggestions)
    # print("-" * 50)
    #
    # # # 测试 6: Typeahead Static 搜索
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.get_typeahead_suggestions("INDUSTRY")
    # print("INDUSTRY搜索建议:", suggestions)
    # print("-" * 50)
    #
    # # # postal code
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.search_typeahead("BING_GEO_POSTAL_CODE", "07304")
    # print("POSTAL_CODE搜索建议:", suggestions)
    # print("-" * 50)
    #
    # # # company
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.search_typeahead("COMPANY", "Google")
    # print("CURRENT_COMPANY搜索建议:", suggestions)
    # print("-" * 50)
    #
    # # # region
    # print("测试 Typeahead 搜索...")
    # suggestions = linkedin_service.query_service.search_typeahead("BING_GEO", "New York")
    # print("REGION搜索建议:", suggestions)
    # print("-" * 50)
    #
    # 【Step 3: Test Management Call & Show number-results (For API)】
    # test_data2={
    #     "keywords":'("project2 manager" OR "program manager") AND (startup OR tech) AND (NOT intern) AND (engineering OR software)',
    #     "filters":[
    #         {
    #             "type": "CURRENT_COMPANY",
    #             "values": [
    #                 {
    #                     "id": "urn:li:organization:2192",
    #                     "text": "Chevron",
    #                     "selectionType": "INCLUDED",
    #                     "parent": {
    #                         "id": "0"
    #                     }
    #                 }
    #             ]
    #         }
    #     ]}
    import json

    test_data2 = json.loads(
        """{"keywords": '"Statistics" OR "Machine Learning"', "filters": [{"type": "INDUSTRY", "values": [{"id": "116", "text": "Transportation, Logistics, Supply Chain and Storage", "selectionType": "INCLUDED"}]}, {"type": "CURRENT_TITLE", "values": [{"text": "International Logistics Specialist", "selectionType": "INCLUDED", "parent": {"id": "0"}}, {"id": "31086", "text": "Global Supply Chain Analyst", "selectionType": "INCLUDED", "parent": {"id": "0"}}, {"id": "9346", "text": "International Trade Manager", "selectionType": "INCLUDED", "parent": {"id": "0"}}, {"text": "International Freight Manager", "selectionType": "INCLUDED", "parent": {"id": "0"}}, {"text": "Global Trade Compliance Officer", "selectionType": "INCLUDED", "parent": {"id": "0"}}, {"id": "9710", "text": "Supply Chain Operations Manager", "selectionType": "INCLUDED", "parent": {"id": "0"}}, {"id": "16207", "text": "International Trade Analyst", "selectionType": "INCLUDED", "parent": {"id": "0"}}]}, {"type": "REGION", "values": [{"id": "104495945", "text": "Carson, California, United States", "selectionType": "INCLUDED"}]}], "limit": 300}"""
    )
    return_search = linkedin_service.filter_query_search(test_data2)
    print(return_search)
    # formatted_user = linkedin_service.formatter.structure_multiple_individuals(return_search['data']['elements'])
    # total_number = linkedin_service.formatter.fetch_total_number(return_search)
    # #
    # #
    # # print(return_search)
    # # print(formatted_user)
    # print(total_number)
    #
