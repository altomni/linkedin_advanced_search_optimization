from linkedin_recruiter_apiservice.api_service import RecruiterService
from jd_understanding_funcs import job_title_adjustment
from llms.chatgpt import ChatGPTWrapper
from utils.synonym_association import get_synonym_associations, Synonym_Associations
from prompts import find_most_likely_entity_name_prompt

import re
import hashlib
from typing import Dict, Optional, Tuple

# ============================================================================
# CACHING MECHANISMS FOR STANDARDIZATION
# ============================================================================

# Cache for standardized inputs - key: (type, input_hash) -> standardized_result
_standardization_cache: Dict[str, Optional[str]] = {}

# Cache for typeahead API results - key: (type, input_hash) -> synonym_list
_typeahead_cache: Dict[str, set] = {}

# Global RecruiterService instance
_global_recruiter_service: Optional[RecruiterService] = None


def _make_standardization_cache_key(type_name: str, input_text: str) -> str:
    """Create a cache key for standardization."""
    combined = f"{type_name}|{input_text.lower().strip()}"
    return hashlib.md5(combined.encode()).hexdigest()


def get_global_recruiter_service() -> RecruiterService:
    """Get or create a global RecruiterService instance."""
    global _global_recruiter_service
    if _global_recruiter_service is None:
        _global_recruiter_service = RecruiterService()
    return _global_recruiter_service


def clear_standardization_caches():
    """Clear all standardization caches."""
    global _standardization_cache, _typeahead_cache
    _standardization_cache.clear()
    _typeahead_cache.clear()
    print("✓ Standardization caches cleared")


def get_standardization_cache_stats() -> Dict[str, int]:
    """Get statistics about standardization cache usage."""
    return {
        "standardization_cache_size": len(_standardization_cache),
        "typeahead_cache_size": len(_typeahead_cache),
    }


def standardize_input(type, input, llm=None):
    """
    Standardize input using LLM association search, similar to linkedin_jd_suggestion_graph.py
    Now with caching for improved performance.

    Args:
        type (str): The type of entity to search for (e.g., "degree", "skill", "company")
        input (str): The input text to standardize
        llm: LLM instance for entity matching (optional, will create DeepSeekWrapper if not provided)

    Returns:
        str: The most likely standardized name from LLM association search
    """
    global _standardization_cache, _typeahead_cache

    # Create cache key
    cache_key = _make_standardization_cache_key(type, input)

    # Check standardization cache first
    if cache_key in _standardization_cache:
        cached_result = _standardization_cache[cache_key]
        print(f"[CACHE HIT] Standardization for '{input}' (type: {type}) -> {cached_result}")
        return cached_result

    if llm is None:
        llm = ChatGPTWrapper()

    # Use global RecruiterService
    rs = get_global_recruiter_service()

    # Check typeahead cache
    if cache_key in _typeahead_cache:
        synonym_list = _typeahead_cache[cache_key]
        print(f"[CACHE HIT] Typeahead for '{input}' (type: {type})")
    else:
        synonym_result = rs.get_typeahead(data={"type": type, "query": input})
        synonym_list = set(
            _["text"]["text"] for _ in synonym_result.get("result", {}).get("result", {}).get("elements", {}))
        # Cache typeahead result
        _typeahead_cache[cache_key] = synonym_list
        print(f"[CACHE MISS] Typeahead for '{input}' (type: {type})")

    if not synonym_list:
        print(f"No synonyms found for '{input}', returning None")
        # Cache the None result
        _standardization_cache[cache_key] = None
        return None

    print("raw_input:", input, "synonym_list:", synonym_list)

    picked_item, input_tokens, output_tokens = llm.invoke(
        find_most_likely_entity_name_prompt.format(
            entity_name=input,
            candidate_item_names=synonym_list
        )
    )

    # Check if LLM returned None or empty string
    if picked_item is None or picked_item.strip() == "" or picked_item.lower() == "none":
        print(f"LLM returned None/empty for '{input}', returning None")
        # Cache the None result
        _standardization_cache[cache_key] = None
        return None

    # Cache the result
    _standardization_cache[cache_key] = picked_item
    print(f"[CACHE MISS] Standardized '{input}' -> '{picked_item}'")
    return picked_item


def clean_text(text):
    """
    Clean text by removing special characters and normalizing

    Args:
        text (str): Input text to clean

    Returns:
        str: Cleaned text
    """
    if not text:
        return ""

    # Remove special characters and normalize
    cleaned = re.sub(r'[^\w\s]', '', str(text).strip())
    return cleaned.lower() if cleaned else ""


def convert_filters_to_recruiter_api_conditions(filters, job_required_main_skills_list, linkedin_enum_params, llm):
    """Returns: dict: Formatted conditions for LinkedIn Recruiter search"""

    """input: 
    job_required_main_skills_list = ["Python","SQL"] 

    filters = {
    # 1. 工作职能 (Job Function)
    "job_function": [
        "Engineering",           # 工程
        "Product Management",    # 产品管理
        "Sales",                # 销售
        "Marketing",            # 市场营销
        # ... 更多职能
    ],

    # 2. 职级 (Seniority Level)
    "seniority": [
        "Director",             # 总监
        "CXO",                 # 高管
        "Manager",             # 经理
        "Senior",              # 高级
        "Entry Level",         # 初级
        # ... 更多职级
    ],

    # 3. 工作年限 (Years of Experience)
    "experience_year": [
        "1-2 years",           # 1-2年
        "3-5 years",           # 3-5年
        "6-10 years",          # 6-10年
        "11+ years",           # 11年以上
        # ... 更多年限范围
    ],

    # 4. 行业 (Industry)
    "industry": [
        "Internet",            # 互联网
        "Software",            # 软件
        "Technology",          # 科技
        "Finance",             # 金融
        # ... 更多行业
    ],

    # 5. 职位标题 (Job Title)
    "job_title": [
        "CTO",                 # 首席技术官
        "VP Engineering",      # 工程副总裁
        "Software Engineer",   # 软件工程师
        "Product Manager",     # 产品经理
        # ... 更多职位
    ],

    # 6. 公司 (Companies)
    "companies": [
        {
            "company_name": "Apple",        # 公司名称 (必需)
            "company_id": "12345"           # 公司ID (可选)
        },
        {
            "company_name": "Microsoft",
            "company_id": "67890"
        },
        # ... 更多公司
    ],

    # 7. 地理位置 (Location)
    "location": {
        # 方式1: 使用邮政编码
        "zipcode": [
            "10001",           # 纽约
            "94102",           # 旧金山
            "90210",           # 洛杉矶
            # ... 更多邮编
        ],

        # 方式2: 使用地区名称 (与zipcode二选一)
        "name": [
            "San Francisco Bay Area",  # 旧金山湾区
            "New York",                # 纽约
            "London",                  # 伦敦
            # ... 更多地区
        ]
    },

    # 8. 关键词 (Keywords) - 可选
    "keywords": "Python developer with AI experience"  # 搜索关键词
    }
    """

    # Function conditions
    """
    job_function: ["Engineering",        
        "Product Management",   
        "Sales"]
    """

    # YOE ??
    """
    "year_of_experience": {"min": 0, "max": 1}
    """

    # industry
    """
    "industry": [
        "Internet",            
        "Software",            
        "Technology",          
        "Finance",             
    ]
    """

    # job title
    """
    "job_title": [
        "CTO",                 
        "VP Engineering",      
        "Software Engineer",   
        "Product Manager",    
    ],
    """

    # companies
    """
    "companies": [
        {
            "company_name": "Apple",        
            "company_id": "12345"           
        },
        {
            "company_name": "Microsoft",
            "company_id": "67890"
        }
    ],
    """

    # location
    """
    "location": {
        "name": [
            "San Francisco Bay Area",  
            "New York",                
            "London",                  
        ]
    }
    """
    payload = {}
    total_input_token = 0
    total_out_token = 0

    if filters.get("companies", []):
        # standardized companies:
        company_list = []
        for _ in filters["companies"]:
            s_company = standardize_input(type="company", input=_)
            if s_company is not None: company_list.append(s_company)
        print(company_list)
        payload["companies"] = [
            {
                "name": _,
                "time_scope": "CURRENT",
                "required": False,
                "selected": True,
                "negated": False
            } for _ in company_list
        ]

    if filters.get("year_of_experience", {}):
        print("get yoe...")
        yoe = filters["year_of_experience"]
        # 兼容两种格式：min/max 和 start_num_year/end_num_year
        min_year = yoe.get("min") or yoe.get("start_num_year")
        max_year = yoe.get("max") or yoe.get("end_num_year")

        if min_year is not None or max_year is not None:
            payload["year_of_experience"] = {}
            payload["year_of_experience"]["start_num_year"] = max(0, min(min_year if min_year is not None else 0, 30))
            payload["year_of_experience"]["end_num_year"] = max(0, min(max_year if max_year is not None else 30, 30))
            print(
                f"  ✅ year_of_experience added: start={payload['year_of_experience']['start_num_year']}, end={payload['year_of_experience']['end_num_year']}")

    if filters.get("location", {}).get("name", []):
        # standardize location
        standardized_location = []
        for _ in filters.get("location", {}).get("name", []):
            s_location = standardize_input(type="geo", input=_)
            if s_location is not None: standardized_location.append(s_location)
        if standardized_location: payload["locations"] = [
            {"name": _, "required": False, "selected": True, "negated": False} for _ in standardized_location]

    if job_required_main_skills_list:
        # payload["keywords"] = (" OR ").join([f'"{_}"' for _ in job_required_main_skills_list])
        # standardize skill
        skills = []
        print("job_required_main_skills_list: ", job_required_main_skills_list)
        for _ in job_required_main_skills_list:
            s_skill = standardize_input(type="skill", input=_)
            if s_skill is not None:
                skills.append(s_skill)
            else:
                skills.append(_)

        if skills: payload["skills"] = [
            {
                "name": _,
                "required": False,
                "selected": True,
                "negated": False
            } for _ in skills
        ]

    if filters.get("industry", []):
        # standardize industry
        standardized_ind = []
        for _ in filters.get("industry", []):
            print("raw ind: ", _)
            s_ind = standardize_input(type="industry", input=_)
            if s_ind is not None: standardized_ind.append(s_ind)
        # ind_data = [_[0] for _ in linkedin_enum_params["industry_data"]]

        if standardized_ind: payload["industries"] = [
            {
                "name": _,
                "required": False,
                "selected": True,
                "negated": False
            }
            for _ in standardized_ind
            # for _ in filters.get("industry", []) if _.title() in ind_data
        ]

    if filters.get("job_function", []):
        jf_data = [_["displayValue"] for _ in linkedin_enum_params["jf_data"]["elements"]]
        payload["job_functions"] = [_.title() for _ in filters.get("job_function", []) if _.title() in jf_data]

    if filters.get("job_title", []):
        # standardize titles
        titles = []
        for origin_job_title in filters["job_title"]:
            # titles.append(_)
            fail_time = 0
            tried_titles = []
            job_title = origin_job_title
            while True:
                s_title = standardize_input(type="occupation", input=job_title)
                if s_title is None:
                    print("Fail to find relevant titles via recruiter typeahead API for : ", job_title)
                    if fail_time >= 5:
                        if origin_job_title not in titles:
                            titles.append(origin_job_title)
                        else:
                            print(f'title {origin_job_title} exists')
                        break
                    # print(f"continue to generate new titles: {job_title}")
                    tried_titles.append(job_title)
                    job_title, input_tokens, output_tokens = job_title_adjustment(llm, job_title, tried_titles)
                    total_input_token += input_tokens
                    total_out_token += output_tokens
                    fail_time += 1
                else:
                    if s_title not in titles:
                        titles.append(s_title)
                    else:
                        print(f'title {s_title} exists')
                    break

        payload["titles"] = [
            {
                "name": _,
                "time_scope": "CURRENT",
                "required": False,
                "negated": False
            } for _ in set(titles)
        ]

    # Handle degrees
    if filters.get("degrees", []):
        standardized_degrees = []
        for _ in filters["degrees"]:
            s_degree = standardize_input(type="degree", input=_)
            if s_degree is not None: standardized_degrees.append(s_degree)
        payload["degrees"] = [
            {
                "name": _,
                "required": False,
                "selected": True,
                "negated": False
            } for _ in standardized_degrees
        ]

    # Handle languages
    if filters.get("language", []):
        print("get language...")
        standardized_languages = []
        # for _ in filters["language"]:
        #     s_lang = standardize_input(type="language", input=_)
        #     if s_lang is not None: standardized_languages.append(s_lang)

        language_data = [_["text"]["text"] for _ in linkedin_enum_params["language_data"]]
        standardized_languages = [_.title() for _ in filters.get("language", []) if _.title() in language_data]
        print('standardized_languages: ', standardized_languages)
        if standardized_languages: payload["languages"] = [
            {
                "name": _,
                "language_proficiency_scope": "PROFESSIONAL_WORKING",  ##NATIVE_OR_BILINGUAL
                "required": True,
                "selected": True,
                "negated": False
            } for _ in standardized_languages
        ]

    # Handle field_of_study
    if filters.get("field_of_study", []):
        standardized_fields = []
        for _ in filters["field_of_study"]:
            s_field = standardize_input(type="field_of_study", input=_)
            if s_field is not None: standardized_fields.append(s_field)
        payload["field_of_study"] = [
            {
                "name": _,
                "required": False,
                "selected": True,
                "negated": False
            } for _ in standardized_fields
        ]

    # Handle graduation_year
    if filters.get("graduation_year", {}):
        grad_year = filters["graduation_year"]
        if grad_year.get("start") or grad_year.get("end"):
            payload["graduation_year"] = {}
            if grad_year.get("start"):
                payload["graduation_year"]["start"] = grad_year["start"]
            if grad_year.get("end"):
                payload["graduation_year"]["end"] = grad_year["end"]

    return {"filters": payload, "input_tokens": total_input_token, "output_tokens": total_out_token}


if __name__ == "__main__":
    # Single Test
    # filters1 = {
    #     # 'industry': ['Transportation, Logistics, Supply Chain and Storage', 'IT Services and IT Consulting'],
    #     # 'job_function': ['Engineering', 'Information Technology'],
    #     'job_title': ['Frontend Engineer', 'Frontend Engineers', 'Front end Engineering'],
    #     # 'location': {'name': ['Long Beach California United States']}, 'seniority': ['senior', 'strategic'],
    #     # 'year_of_experience': {'start_num_year': 4, 'end_num_year': 15}
    #     "language": ["English", "Chinese"],
    #     }
    #
    # skills1 = ['Server Engineering Leadership', 'Scalable System', 'machine learning']

    # filters1 = {
    #     'job_title': ['Machine Learning Engineer'],
    #     # 'industry': ['Motor Vehicle Manufacturing', 'Robotics Manufacturing'],
    #     'job_function': ['Engineering'],
    #     'location': {'name': ['San Francisco Bay Area']},
    #     'seniority': ['senior'],
    #     'year_of_experience': {'start_num_year': 2, 'end_num_year': 4},
    #     "language": ["English", "Chinese"],
    # }
    # skills1 = ['Reinforcement Learning', 'Machine Learning']

    # {"location": {"name": ["San Francisco Bay Area"]}, "seniority": ["senior"], "job_function": ["Engineering"],
    #  "industry": ["Motor Vehicle Manufacturing", "Software Development", "IT Services and IT Consulting"],
    #  "job_title": ["Machine Learning Engineer, Reinforcement Learning", "Autonomous Driving Machine Learning Engineer",
    #                "Reinforcement Learning Research Engineer"], "language": ["Chinese"],
    #  "year_of_experience": {"min": 2, "max": 4}}

    # filters1 = {
    #     'job_title': ["Machine Learning Engineer", "Autonomous Driving Machine Learning Engineer",
    #                 "Reinforcement Learning Research Engineer"],
    #     'industry': ["Motor Vehicle Manufacturing", "Software Development", "IT Services and IT Consulting"],
    #     'job_function': ['Engineering'],
    #     'location': {'name': ['San Francisco Bay Area']},
    #     'seniority': ['senior'],
    #     'year_of_experience': {'start_num_year': 2, 'end_num_year': 4},
    #     "language": ["Chinese"],
    # }
    # skills1 = ["Reinforcement Learning", "RLHF", "Multi-Agent Systems", "Simulation", "Autonomous Driving"]

    # {"location": {
    #     "name": ["New York City Metropolitan Area", "Greater Chicago Area", "Charleston South Carolina United States"]},
    #  "seniority": ["senior"], "job_function": ["Engineering", "Finance", "Research"],
    #  "job_title": ["High-Frequency Trading Software Engineer", "Quantitative Developer", "C++ Software Developer"],
    #  "language": ["Chinese"], "year_of_experience": {"min": 3, "max": 10}}

    # filters1 = {
    #     'job_title': ["High-Frequency Trading Software Engineer", "Quantitative Developer", "C++ Software Developer"],
    #     # 'industry': ["Motor Vehicle Manufacturing", "Software Development", "IT Services and IT Consulting"],
    #     'job_function': ["Engineering", "Finance", "Research"],
    #     'location': {'name': ["New York City Metropolitan Area", "Greater Chicago Area", "Charleston South Carolina United States"]},
    #     'seniority': ['senior'],
    #     'year_of_experience': {'start_num_year': 3, 'end_num_year': 10},
    #     "language": ["Chinese"],
    # }
    # skills1 = ["High-Frequency Trading", "Market Data Interfaces", "Low-Latency Optimization"]

    # filters1 = {
    #     'job_title': ["Data Engineer", "Junior Data Engineer", "Data Engineering Analyst"],
    #     # 'industry': ["Motor Vehicle Manufacturing", "Software Development", "IT Services and IT Consulting"],
    #     'job_function': ["Engineering"],
    #     'location': {'name': ["San Francisco Bay Area"]},
    #     # 'seniority': ["entry level"],
    #     # 'year_of_experience': {"min": 1, "max": 30},
    #     "language": ["Chinese"],
    # }
    # skills1 = ["Data Pipeline", "Data Warehousing", "Data Quality Assurance"]

    # filters1 = {"location": {"name": ["San Francisco Bay Area"]}, "seniority": ["entry level"],
    #             # "job_function": ["Engineering"],
    #  # "industry": ["Financial Services", "IT Services and IT Consulting", "Software Development"],
    #  "job_title": ["Data Engineer", "Data Pipeline Engineer", "ETL Engineer"], "language": ["Chinese"],
    #  "year_of_experience": {"start_num_year": 1, "end_num_year": 2}}
    # skills1 = ["Data Pipeline", "Data Warehousing", "Data Infrastructure"]

    # filters1 = {
    #     'companies': ['Oracle', 'Amazon Web Services (AWS)', 'Microsoft'],
    #     'industry': ['Travel Arrangements', 'IT Services and IT Consulting', 'Software Development'],
    #     'job_function': ['Engineering'],
    #     'job_title': ['Site Reliability Engineer', 'DevOps Engineer'],
    #     'language': ['Chinese'],
    #     'location': {'name': ['San Francisco Bay Area']}, 'seniority': ['senior'],
    #     'year_of_experience': {'end_num_year': 15, 'start_num_year': 3}}
    #
    # skills1 = ['Site Reliability Engineering', 'Security Incident Response', 'IT Services and IT Consulting']

    filters1 = {"location": {"name": ["Guangzhou Guangdong China", "Beijing China", "Foshan Guangdong China", "Dongguan Guangdong China", "Zhongshan Guangdong China", "Jiangmen Guangdong China", "Huizhou Guangdong China", "Shenzhen Guangdong China", "Zhuhai Guangdong China", "Zhaoqing Guangdong China", "Qingyuan Guangdong China", "Hong Kong SAR", "Macao SAR", "Shaoguan Guangdong China", "Heyuan Guangdong China", "Yangjiang Guangdong China", "Shanwei Guangdong China", "Meizhou Guangdong China"]},
                "seniority": ["director"],
                "job_function": ["Quality Assurance", "Program and Project Management", "Engineering"],
                "job_title": ["Head of Quality Assurance", "QA Manager", "Quality Engineering Lead"],   # item: increase num, remove all: remove category filter, increase num
                "Industry": ["IT Services and IT Consulting", "Software Development"],
                "year_of_experience": {"start_num_year": 2, "end_num_year": 12}}
    # skills1 = ["Full Set Accounting", "Tax Compliance",
    #            "Payroll Administration", "Mandarin"
    #            ]
    skills1 = ["Automated Software Testing", "Quality Control", "Machine Learning", "Java"] #item: increase num, remove all: remove category filter, increase num

    # filters1 = {"location": {"name":
    #                          # ['Greater Kota Kinabalu Sabah Malaysia'],
    #                              ["China"],
    #                          # ['West Coast Division, Sabah, Malaysia'],
    #                          },
    #             "seniority": ["senior", "strategic"],
    #             "job_title": ["Process Chemist", "Chemical Process Engineer", "Research Chemist"],
    #             # #["Senior Accounts Executive", "Senior Accountant", "Accounting Supervisor", "Senior Finance Executive"],
    #             #                  #
    #             # "language": ["Japanese", "German", "Korean", "French", "Spanish", "English"],
    #             "year_of_experience": {"start_num_year": 3, "end_num_year": 15},
    #             # "companies": ["Hengrui", "Yangtze River Pharmaceutical", "Qilu Pharmaceutical", "Novartis", "Dizal"],
    #             }
    # # skills1 = ["Full Set Accounting", "Tax Compliance",
    # #            "Payroll Administration", "Mandarin"
    # #            ]
    # # skills1 = ["Organic Synthesis", "Process Development", "Route Design", "Process Optimization"]
    # skills1 = []


    llm = ChatGPTWrapper()

    from config.linkedin_enums import get_linkedin_enum_data

    linkedin_enum_params = get_linkedin_enum_data()
    search_condition = convert_filters_to_recruiter_api_conditions(filters1, skills1, linkedin_enum_params, llm)
    # print("search_condition:", search_condition)
    # search_condition = {
    #     'filters': {
    #         # 'companies': [
    #         #     {'name': 'Oracle', 'time_scope': 'CURRENT', 'required': False, 'selected': True, 'negated': False},
    #         #     {'name': 'Amazon Web Services (AWS)', 'time_scope': 'CURRENT', 'required': False, 'selected': True,
    #         #      'negated': False},
    #         #     {'name': 'Microsoft', 'time_scope': 'CURRENT', 'required': False, 'selected': True, 'negated': False}],
    #         'year_of_experience': {'start_num_year': 3, 'end_num_year': 15},
    #         'locations': [
    #             {'name': 'San Francisco Bay Area', 'required': False, 'selected': True, 'negated': False}],
    #         'skills': [
    #             {'name': 'Site Reliability Engineering', 'required': False, 'selected': True, 'negated': False},
    #             {'name': 'Security Incident Response', 'required': False, 'selected': True, 'negated': False}],
    #         'industries': [{'name': 'IT Services and IT Consulting', 'required': False, 'selected': True,
    #                         'negated': False}],
    #         'job_functions': ['Engineering'], 'titles': [
    #             {'name': 'Site Reliability Engineer', 'time_scope': 'CURRENT', 'required': False, 'negated': False},
    #             {'name': 'DevOps Engineer', 'time_scope': 'CURRENT', 'required': False, 'negated': False}],
    #         'languages': [
    #             {'name': 'Chinese', 'language_proficiency_scope': 'PROFESSIONAL_WORKING', 'required': False,
    #              'selected': True, 'negated': False}]}, 'input_tokens': 0, 'output_tokens': 0}

    rs = RecruiterService()
    result = rs.get_search_num(search_condition)
    print('Result number: ', result['num'])

    # candidate_result = rs.get_search_results(search_condition)
    # for idx, candidate in enumerate(candidate_result['results']):
    #     name = candidate['memberProfileResolutionResult']['firstName'] + ' ' + \
    #            candidate['memberProfileResolutionResult']['lastName']
    #     industry = candidate['memberProfileResolutionResult']['industryName']
    #     location = candidate['memberProfileResolutionResult']['location']['displayName']
    #     education = candidate['memberProfileResolutionResult']['educations']
    #     print(f"Candidate {idx} | Location: {location} | Industry: {industry}")
    #     print(f"\t\tEducation: {education}")

    # Batch Test
    # # Example 1: Basic skill and company search
    # for _ in range(10):
    #     print("=== Example 1: Basic skill and company search ===")
    #     filters1 = {
    #         'industry': ['Software Development'],
    #         'job_function': ['Engineering'],
    #         'job_title': ['Lead Backend Engineer'],
    #         'location': {'name': ['Mountain View California United States']},
    #         'seniority': ['director', 'strategic'],
    #         'year_of_experience': {'start_num_year': 10, 'end_num_year': 15}
    #     }

    # filters1 = {
    #         "companies": ["Google"],
    #         "job_title": ["Software Engineer", "Data Scientist"],
    #         # "location": {"name": ["San Francisco", "New York"]},
    #         "job_function": ["Engineering"],
    #         "language": ["English"],
    #     'year_of_experience': {'min': 10},
    #     'titles': [
    #         {'name': 'Autonomous Systems Engineer', 'time_scope': 'CURRENT', 'required': True, 'selected': True,
    #          'negated': False},
    #         {'name': 'Hardware Architect', 'time_scope': 'CURRENT', 'required': True, 'selected': True,
    #          'negated': False}],
    #     'industry': ["Software Development"],
    #
    #     'location':  {'name': ['Palo Alto CA', 'San Francisco Bay Area CA']}
    # }
    #
    # skills1 = ['Autonomous Driving Systems', 'Heterogeneous Computing']

    #     skills1 = ['Server Engineering Leadership', 'Scalable System']
    #     skills2 = ['Server Engineering Leadership', 'Scalable System', 'machine learning']
    #     # skills1 = ['CI/CD', 'Database']
    #     # skills2 = ['CI/CD', 'Database', 'machine learning', 'data pipeline']
    #     llm = ChatGPTWrapper()
    #
    #     from config.linkedin_enums import get_linkedin_enum_data
    #
    #     linkedin_enum_params = get_linkedin_enum_data()
    #     result1 = convert_filters_to_recruiter_api_conditions(filters1, skills1, linkedin_enum_params, llm)
    #     # print("Result 1:", result1)
    #     rs = RecruiterService()
    #     result = rs.get_search_num(result1)
    #     print('skill1: ', result['num'])
    #
    #     result1 = convert_filters_to_recruiter_api_conditions(filters1, skills2, linkedin_enum_params, llm)
    #     # print("Result 1:", result1)
    #     result = rs.get_search_num(result1)
    #     print('skill2: ', result['num'])
    #
    #     print("============================")
