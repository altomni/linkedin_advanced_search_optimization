import os
import random

# =============================================================================
# Database Connection Configuration
# =============================================================================

class RedisConfig:
    HOST     = os.getenv("REDIS_HOST", "localhost")
    PORT     = int(os.getenv("REDIS_PORT", "6379"))
    DB       = int(os.getenv("REDIS_DB", "0"))
    PASSWORD = os.getenv("REDIS_PASSWORD") or None


class MongoDBConfig:
    # MONGO_URI 直接使用完整连接串（含认证信息）
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DB_NAME   = os.getenv("MONGO_DB_NAME", "aisourcing")

    # Collection names
    DETAIL_PROFILE_COLLECTION = os.getenv("MONGODB_DETAIL_PROFILE_COLLECTION", "detail_profile")
    SEARCH_PROFILE_COLLECTION = os.getenv("MONGODB_SEARCH_PROFILE_COLLECTION", "search_profile")

    # Connection pool (pymongo MongoClient 参数)
    MAX_POOL_SIZE            = int(os.getenv("MONGO_MAX_POOL_SIZE",                 "20"))
    MIN_POOL_SIZE            = int(os.getenv("MONGO_MIN_POOL_SIZE",                  "2"))
    SERVER_SELECTION_TIMEOUT = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))
    CONNECT_TIMEOUT          = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS",          "5000"))
    SOCKET_TIMEOUT           = int(os.getenv("MONGO_SOCKET_TIMEOUT_MS",          "30000"))


class MySQLConfig:
    HOST     = os.getenv("DB_HOST",     "localhost")
    PORT     = int(os.getenv("DB_PORT", "3306"))
    DB       = os.getenv("DB_NAME",     "aisourcing")
    USERNAME = os.getenv("DB_USER",     "root")
    PASSWORD = os.getenv("DB_PASSWORD", "")

    # Connection pool (SQLAlchemy create_engine 参数)
    POOL_SIZE    = int(os.getenv("DB_POOL_SIZE",    "10"))
    MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))  # 防止 "MySQL has gone away"
    POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() in ("true", "1", "yes")

    @classmethod
    def uri(cls) -> str:
        return (
            f"mysql+pymysql://{cls.USERNAME}:{cls.PASSWORD}"
            f"@{cls.HOST}:{cls.PORT}/{cls.DB}?charset=utf8mb4"
        )


# =============================================================================
# Rerank Monitor Configuration
# =============================================================================

class RerankMonitorConfig:
    MAX_CONCURRENT    = int(os.getenv("RERANK_MAX_CONCURRENT",    "16"))
    POLL_INTERVAL     = int(os.getenv("RERANK_POLL_INTERVAL",      "3"))   # seconds
    STALE_TIMEOUT     = int(os.getenv("RERANK_STALE_TIMEOUT",    "600"))   # seconds
    CACHE_TTL         = int(os.getenv("RERANK_CACHE_TTL",        "7200"))  # seconds (2h)
    MAX_RETRIES       = int(os.getenv("RERANK_MAX_RETRIES",         "2"))
    DEFAULT_RERANK_NUM = int(os.getenv("RERANK_DEFAULT_RERANK_NUM", "50"))  # task.rerank_num 为 None 时的兜底值
    COLL_TIMEOUT      = int(os.getenv("RERANK_COLL_TIMEOUT",     "14400"))  # seconds (4h) coll 超时强制完成
    REDIS_KEY_TTL     = int(os.getenv("RERANK_REDIS_KEY_TTL",   "604800"))  # seconds (7d) Redis key 过期时间
    LLM_PROVIDER      = os.getenv("RERANK_LLM_PROVIDER", "gemini")          # "gemini" or "gpt"
    LLM_MODEL         = os.getenv("RERANK_LLM_MODEL", "gemini-3-flash-preview")

SEARCH_CHANNEL = os.getenv('SEARCH_CHANNEL', 'recruiter') # sales nav

# LiteLLM proxy config (backward compatible)
# If LITELLM_BASE_URL is set, route all OpenAI calls through LiteLLM proxy.
# If not set, fall back to standard OpenAI API (base_url=None).
LITELLM_BASE_URL = os.getenv('LITELLM_BASE_URL')  # e.g. http://192.168.1.27:4000/v1
LITELLM_API_KEY = os.getenv('LITELLM_API_KEY')

# Multi-key rotation: comma-separated OpenAI keys for random load balancing
# e.g. OPENAI_API_KEYS=sk-aaa,sk-bbb,sk-ccc
_openai_api_keys_str = os.getenv('OPENAI_API_KEYS', '')
OPENAI_API_KEYS = [k.strip() for k in _openai_api_keys_str.split(',') if k.strip()]

def get_openai_api_key():
    """Pick an API key with priority: LiteLLM > multi-key random > single OPENAI_API_KEY."""
    if LITELLM_API_KEY:
        return LITELLM_API_KEY
    if OPENAI_API_KEYS:
        return random.choice(OPENAI_API_KEYS)
    return os.environ.get("OPENAI_API_KEY")

def get_openai_base_url():
    """Return LiteLLM base URL if available, otherwise None (OpenAI default)."""
    return LITELLM_BASE_URL

def get_gemini_api_key():
    """Return Gemini API key from environment."""
    return os.environ.get("GEMINI_API_KEY")

def get_deepseek_api_key():
    """Return DeepSeek API key from environment."""
    return os.environ.get("DEEPSEEK_API_KEY")

def get_dashscope_api_key():
    """Return DashScope (Qwen) API key from environment."""
    return os.environ.get("DASHSCOPE_API_KEY")

# Reparse graph mode configuration
# When True, use selective reparse graph (only re-run affected nodes) instead of full graph
# When False, fall back to full parsing graph even when reparse_value is present
REPARSE_USE_SELECTIVE_GRAPH = os.getenv('REPARSE_USE_SELECTIVE_GRAPH', 'true').lower() in ('true', '1', 'yes')

# Reparse value mode configuration
# Controls how get_reparse_value() returns values when a key is found
# Valid values: "key_value" (returns "key: value"), "form_value" (returns form_value), "skip" (returns None)
REPARSE_VALUE_MODE = os.getenv('REPARSE_VALUE_MODE', 'key_value')

# Reparse target statuses configuration
# Controls which statuses are processed by parse_reparse_value()
# Default: dismissed,customized (comma-separated list)
_reparse_statuses_str = os.getenv('REPARSE_TARGET_STATUSES', 'dismissed,customized')
REPARSE_TARGET_STATUSES = [s.strip() for s in _reparse_statuses_str.split(',') if s.strip()]

# LLM parsing defaults
DEFAULT_PARSE_TEMPERATURE = float(os.getenv('DEFAULT_PARSE_TEMPERATURE', '0.1'))
DEFAULT_LLM_PARSE_MAX_RETRIES = int(os.getenv('DEFAULT_LLM_PARSE_MAX_RETRIES', '3'))

# Language filtering configuration
MAX_CONCURRENT_LANGUAGE_LLM_CALLS = int(os.getenv('MAX_CONCURRENT_LANGUAGE_LLM_CALLS', '10'))

# Language filter minimum recommendation level
# Candidates with this level or below will be filtered out
# Valid values: PASS, LIKELY_PASS, PARTIAL, FAIL (default: FAIL)
# Example: If set to PARTIAL, both PARTIAL and FAIL candidates will be filtered
LANGUAGE_FILTER_MIN_LEVEL = os.getenv('LANGUAGE_FILTER_MIN_LEVEL', 'FAIL').upper()

# Two-round search configuration
# Minimum candidates from first search (with language) to skip second round (default: 10)
LANGUAGE_SEARCH_MIN_THRESHOLD = int(os.getenv('LANGUAGE_SEARCH_MIN_THRESHOLD', '10'))
# Maximum candidates to retrieve in second search (without language) (default: 800)
LANGUAGE_SECOND_SEARCH_MAX = int(os.getenv('LANGUAGE_SECOND_SEARCH_MAX', '800'))

# ==================== Perspective Definitions for Conflict Detection ====================
# Used by find_conflict_info.py and enrich_metrics.py for JD conflict analysis

PERSPECTIVE_DEF_MAP = {
    "title": "The exact name of the position being hired for, including level and focus (for example, 'Senior Backend Engineer', 'Sales Manager'). This is the label that will appear on the job description and offer letter.",
    "jobFunctions": "The main kind of work the role belongs to, such as building software, selling products, analyzing data, or managing teams—independent of the job title.",
    "salary": "The compensation the employer is willing to offer for this role, usually given as a range, with currency, period (year, month, or hour), and whether variable pay (bonus, commission, equity) is included. This should reflect the realistic target range for qualified candidates, not an unlimited budget.",
    "degree": "The minimum formal education level required for the role (for example, bachelor’s, master’s, PhD), optionally including field(s) of study if they are truly required. If education is flexible or can be substituted by experience, that should be stated here.",
    "experience": "The required amount and type of **professional work history** a candidate must have (e.g., years of experience, specific role history, seniority). **Strictly EXCLUDE formal education degrees (Bachelor/Master/PhD) from this perspective as they are covered by 'degree'.**",
    # "requiredSkills": "The essential abilities, tools, and knowledge a candidate must already have; candidates missing these are not considered qualified. RequiredSkills means concrete detailed skill, not experience.",
    "requiredSkills": """The specific abilities, tools, and knowledge that a candidate must possess and be able to demonstrate at the time of application. These are clearly defined competencies (such as proficiency with certain software, instruments, or programming languages) that are necessary to perform the core job duties. Experience or preferences (such as years of work or familiarity with standards) should not be included under Required Skills.""",
    "preferredIndustry": "The industries in which the employer prefers candidates to have experience, such as fintech, healthcare, automotive, or e-commerce. ",
    "requiredLanguages": "Human languages that the candidate must be able to speak, read, or write at a specified proficiency level to perform the job effectively (for example, 'English – fluent', 'Mandarin – business level'). Candidates lacking these proficiency levels should be considered unqualified.",
    "location": "The primary work location for the role, including city and country, plus the work arrangement (onsite, hybrid, or fully remote) and any constraints (for example, 'must be based in EU', 'must be willing to relocate to Singapore', 'remote within US time zones'). It's common for international company to hire candidate for an office in another country ",
    "preferredCompanies": "Specific companies that the employer values as prior workplaces for candidates—often competitors, partners, or reputable firms in similar domains. "
}