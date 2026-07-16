"""Response schemas for the v3 optimize-search API (mirrors api/v2 of the parent repo,
plus the v3-only multi-archetype fields)."""
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    error: str


class OptimizeSearchResponse(BaseModel):
    """Optimized search conditions from advanced_search_optimization_v3.single_process."""
    # Optimized search conditions
    final_skills: List[str] = Field(..., description="Final optimized skill list")
    final_conditions: Dict[str, Any] = Field(..., description="Final optimized search conditions")
    final_count: int = Field(..., description="Merged-union result count after optimization")
    format_filter_conditions: Dict[str, Any] = Field(..., description="Recruiter API format conditions for batch search")

    # v3 MULTI-ARCHETYPE additions: one optimized condition per archetype (+ widened variants).
    # A caller can fetch each archetype's format_filter_conditions separately and union the results.
    archetypes: Optional[List[Dict[str, Any]]] = Field(
        None, description="Per-archetype optimized conditions (label, final_count, "
                          "format_filter_conditions, relaxed, widen_geo, binding_enforced)")
    n_archetypes: Optional[int] = Field(None, description="Number of archetype conditions returned")
    linkedin_count_calls: Optional[int] = Field(
        None, description="LinkedIn count-probe API calls with distinct conditions made during optimization")

    # Optimization explanation
    rewritten_optim_path: str = Field("", description="Human-readable optimization path for frontend display")

    # Pass-through from parsing (needed for a downstream batch-search step)
    job_id: Optional[str] = None
    job_desc: Optional[str] = None
    job_summary: Optional[str] = None
    job_location_list: Optional[List[str]] = None
    job_search_language: Optional[List[str]] = Field(None, description="Language requirements for candidate filtering")
    prefer_candidate_companies: Optional[Union[str, List[str]]] = Field(None, description="Preferred companies for candidate matching")
    hiring_company: Optional[str] = None

    # Year requirements (passed through for downstream search metadata)
    job_required_min_years: Optional[int] = None
    job_required_max_years: Optional[int] = None
    suggest_min_year: Optional[int] = None
    suggest_max_year: Optional[int] = None

    all_skills: Optional[List[str]] = None
    mandatory_skills: Optional[List[Dict[str, Any]]] = None
    good_to_have_skills: Optional[List[Dict[str, Any]]] = None
    job_required_main_skills_list: List[str] = Field(..., description="Same as final_skills, for batch-search compatibility")
    search_channel: Optional[str] = None

    # Additional fields for search-candidates compatibility
    job_main_skills: Optional[List[str]] = Field(None, description="Mapped from final_skills for search graph compatibility")
    search_results_num: Optional[int] = Field(None, description="Mapped from final_count for search graph compatibility")

    # Metadata
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    run_time_stats: Optional[Dict[str, float]] = None
    llm_provider: Optional[str] = Field(None, description="LLM vendor used (e.g. openai)")
    llm_model: Optional[str] = Field(None, description="LLM model used (e.g. gpt-4.1)")

    # Diagnostics
    errors: Optional[List[str]] = None
    trace: Optional[List[str]] = None
    message: Optional[str] = None