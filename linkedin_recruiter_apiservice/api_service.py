# from config.recruiter_api_config import RECRUITER_API_BASE_URL
from config.api_config import LinkedInConfig
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict, Any
import time


class RecruiterService:
    def __init__(self):
        self.base_url = LinkedInConfig.RECRUITER_API_BASE_URL
        self.headers = {"Content-Type": "application/json"}

        # Validate base URL
        if not self.base_url:
            raise ValueError("RECRUITER_API_BASE_URL environment variable is not set")
        if not self.base_url.startswith(('http://', 'https://')):
            raise ValueError(f"Invalid base URL format: {self.base_url}. Must start with http:// or https://")

        # Configure session with retry strategy
        self.session = self._create_session_with_retries()

    def _create_session_with_retries(self) -> requests.Session:
        """Create a requests session with automatic retry on network errors"""
        session = requests.Session()

        # Retry strategy: retry on connection errors, timeouts, and 5xx errors
        retry_strategy = Retry(
            total=3,  # Total number of retries
            backoff_factor=2,  # Wait 2s, 4s, 8s between retries
            status_forcelist=[500, 502, 503, 504],  # Retry on these HTTP status codes
            allowed_methods=["POST"],  # Allow retries on POST requests
            raise_on_status=False  # Don't raise exception on retry exhaustion
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def _make_request(self, endpoint: str, data: Dict[str, Any], max_retries: int = 3, timeout: int = 300) -> Dict[str, Any]:
        """Unified request handler for all API calls with retry logic for network errors

        Args:
            endpoint: API endpoint path
            data: Request payload
            max_retries: Maximum number of manual retries for ChunkedEncodingError (default: 3)
            timeout: Request timeout in seconds (default: 300s = 5min for large responses)
        """
        import json
        from requests.exceptions import ChunkedEncodingError, ConnectionError, Timeout

        last_exception = None

        # Manual retry loop for ChunkedEncodingError (not handled by Retry adapter)
        for attempt in range(max_retries):
            try:
                print(f"  [API Request] Attempt {attempt + 1}/{max_retries}: {endpoint}")

                response = self.session.post(
                    f"{self.base_url}{endpoint}",
                    json=data,
                    headers=self.headers,
                    timeout=timeout,  # 5 minute timeout for large responses
                    stream=False  # Don't use streaming to avoid incomplete reads
                )

                # Log request data if response is not 200
                if response.status_code != 200:
                    print(f"\n{'='*80}")
                    print(f"❌ [API ERROR] Non-200 Response: {response.status_code}")
                    print(f"{'='*80}")
                    print(f"Endpoint: {endpoint}")
                    print(f"Full URL: {self.base_url}{endpoint}")
                    print(f"\nRequest Data:")
                    try:
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                    except:
                        print(repr(data))
                    print(f"\nResponse Text: {response.text[:500]}")
                    print(f"{'='*80}\n")

                response.raise_for_status()  # Automatically handle HTTP errors
                return response.json()

            except (ChunkedEncodingError, ConnectionError, Timeout) as e:
                last_exception = e
                error_type = type(e).__name__
                print(f"  [WARNING] {error_type} on attempt {attempt + 1}/{max_retries}: {str(e)[:200]}")

                if attempt < max_retries - 1:
                    # Exponential backoff: 2s, 4s, 8s
                    wait_time = 2 ** (attempt + 1)
                    print(f"  [RETRY] Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                else:
                    print(f"  [ERROR] All {max_retries} attempts failed for {endpoint}")
                    raise

            except Exception as e:
                # For other exceptions, don't retry
                print(f"  [ERROR] Non-retryable error: {type(e).__name__}: {str(e)[:200]}")
                raise

        # If we get here, all retries failed
        if last_exception:
            raise last_exception

    def get_search_num(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Get the number of search results based on filters"""
        """{
            "filters": {
                "companies": [{"name": "Google", "time_scope": "CURRENT", "required": "true", "selected": "true", "negated": "false"}],
                "year_of_experience": {"start_num_year": 1, "end_num_year": 5},
                "locations": [{"name": "United States", "required": "false", "selected": "true", "negated": "false"}]
            }
        }"""
        return self._make_request("/api/query/num-by-filters", data)

    def get_search_results(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed search results based on filters"""
        """{
            "filters": {
                "companies": [{"name": "Google", "time_scope": "CURRENT", "required": "true", "selected": "true", "negated": "false"}],
                "year_of_experience": {"start_num_year": 1, "end_num_year": 5},
                "locations": [{"name": "United States", "required": "false", "selected": "true", "negated": "false"}]
            }
        }"""
        return self._make_request("/api/query/search-results", data)

    def get_typeahead(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Get typeahead suggestions for search queries"""
        """payload = {"type": "degree", "query": "Bachelor"}"""
        """{'query': 'Chiropractors', 'result': {'result': {'elements': [], 'metadata': {'id': ''}, 'paging': {'count': 10, 'links': [], 'start': 0, 'total': 0}, 'status': 200}}, 'success': True, 'type': 'industry'}"""
        return self._make_request("/api/typeahead", data)


if __name__ == "__main__":
    try:
        rs = RecruiterService()
        print(f"Base URL: {rs.base_url}")
        # search_conditions = {'filters':
        #                          {'job_functions': ['Engineering'],
        #                           'locations': [
        #                               {'name': 'California, United States', 'negated': False, 'required': False,
        #                                'selected': True},
        #                               {'name': 'Washington, United States', 'negated': False, 'required': False,
        #                                'selected': True},
        #                               {'name': 'Oregon, United States', 'negated': False, 'required': False,
        #                                'selected': True},
        #                               {'name': 'Colorado, United States', 'negated': False, 'required': False,
        #                                'selected': True}],
        #                           'skills': [
        #                               {'name': 'REST APIs', 'negated': False, 'required': False, 'selected': True},
        #                               {'name': 'Data Synchronization', 'negated': False, 'required': False,
        #                                'selected': True}],
        #                           'titles': [{'name': 'Back End Developer', 'negated': False, 'required': False,
        #                                       'time_scope': 'CURRENT'},
        #                                      {'name': 'Back End Developer', 'negated': False, 'required': False,
        #                                       'time_scope': 'CURRENT'}]}
        #                      }

        # search_conditions = {'filters': {
        #     'skills': [{'name': 'Customer Onboarding', 'required': False, 'selected': True, 'negated': False},
        #                {'name': 'Project Management', 'required': False, 'selected': True, 'negated': False}],
        #     'job_functions': ['customer success and support', 'program and project management'], 'titles': [
        #         {'name': 'Customer Success Manager', 'time_scope': 'CURRENT', 'required': False, 'negated': False},
        #         {'name': 'Project Manager', 'time_scope': 'CURRENT', 'required': False, 'negated': False}]},
        #                      'input_tokens': 0, 'output_tokens': 0}

        search_conditions = {'filters': {'year_of_experience': {'start_num_year': 0, 'end_num_year': 10}, 'locations': [
            {'name': 'Beijing, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Shanghai, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Shenzhen, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Hangzhou, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Guangzhou, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Dongguan, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Foshan, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Huizhou, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Shaoxing, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Huzhou, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Jiaxing, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Zhongshan, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Zhuhai, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Jiangmen, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Zhaoqing, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Ningbo, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Suzhou, Jiangsu, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Wuxi, Jiangsu, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Hong Kong SAR', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Macao SAR', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Shantou, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Heyuan, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Shaoguan, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Yangjiang, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Meizhou, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Chaozhou, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Jieyang, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Yunfu, Guangdong, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Nanjing, Jiangsu, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Taizhou, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Changzhou, Jiangsu, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Yangzhou, Jiangsu, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Jinhua, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Zhenjiang, Jiangsu, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Quzhou, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Zhoushan, Zhejiang, China', 'required': False, 'selected': True, 'negated': False},
            {'name': 'Yancheng, Jiangsu, China', 'required': False, 'selected': True, 'negated': False}],
                                         'skills': [
            {'name': '协议逆向', 'required': False, 'selected': True, 'negated': False},
            {'name': '分布式采集架构', 'required': False, 'selected': True, 'negated': False},
            {'name': '浏览器自动化', 'required': False, 'selected': True, 'negated': False},
            {'name': 'IP 池管理', 'required': False, 'selected': True, 'negated': False}],
                                         'titles': [
            {'name': 'Data Engineer', 'time_scope': 'CURRENT', 'required': False, 'negated': False},
            {'name': 'Security Engineer', 'time_scope': 'CURRENT', 'required': False, 'negated': False}]},
                             'input_tokens': 942, 'output_tokens': 10}

        # search_conditions = {'filters': {'year_of_experience': {'start_num_year': 2, 'end_num_year': 4}, 'locations': [
        #     {'name': 'San Francisco Bay Area', 'required': False, 'selected': True, 'negated': False}], 'skills': [
        #     {'name': 'Reinforcement Learning', 'required': False, 'selected': True, 'negated': False},
        #     {'name': 'RLHF', 'required': False, 'selected': True, 'negated': False},
        #     {'name': 'Multi-agent Systems', 'required': False, 'selected': True, 'negated': False},
        #     {'name': 'Modeling and Simulation', 'required': False, 'selected': True, 'negated': False},
        #     {'name': 'Autonomous Vehicles', 'required': False, 'selected': True, 'negated': False}], 'industries': [
        #     {'name': 'Motor Vehicle Manufacturing', 'required': False, 'selected': True, 'negated': False},
        #     {'name': 'Software Development', 'required': False, 'selected': True, 'negated': False},
        #     {'name': 'IT Services and IT Consulting', 'required': False, 'selected': True, 'negated': False}],
        #              'job_functions': ['Engineering'],
        #         'titles': [
        #         {'name': 'Machine Learning Engineer', 'time_scope': 'CURRENT', 'required': False, 'negated': False}],
        #              'languages': [
        #                  {'name': 'Chinese', 'language_proficiency_scope': 'NATIVE_OR_BILINGUAL', 'required': False,
        #                   'selected': True, 'negated': False}]}, 'input_tokens': 666, 'output_tokens': 9}


        # {"type": "skill", "query": "Python (Programming Language)"}
        result = rs.get_search_num(search_conditions)
        print("Result:", result['num'])
    except Exception as e:
        print(f"Error: {e}")
