"""Request schemas for the v3 optimize-search API (mirrors api/v2 schemas of the parent repo)."""
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class OptimizeSearchRequest(BaseModel):
    """Request for the v3 multi-archetype search optimization endpoint.

    Same contract as the parent repo's /v2/optimize-search (extra fields such as
    apn_display_value are accepted and ignored — the standalone deploy has no
    highlight service).
    """
    parsing_result: Dict[str, Any] = Field(
        ...,
        description="Full parsing result from the parsing endpoint (job_title_info, "
                    "job_function_info, company_industry_info, job_seniority_info, "
                    "job_location_list, mandatory_skills, job_desc, ...)"
    )
    job_id: Optional[str] = Field(default="", description="Job ID for tracking")
    search_channel: Literal["recruiter", "sales_nav"] = Field(default="recruiter", description="Search channel to use")
    enable_tracing: bool = Field(default=False, description="Enable LLM tracing")
    min_target: Optional[int] = Field(default=200, ge=1, description="Minimum target for result count during optimization")
    max_target: Optional[int] = Field(default=600, ge=1, description="Maximum target for result count during optimization")
    include_language_in_search: bool = Field(
        default=True,
        description="Include language in LinkedIn API search conditions. When disabled, "
                    "language filtering happens post-search."
    )