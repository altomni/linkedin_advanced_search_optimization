"""
advanced_search_optimization_v3:
  • AUTO-WIDEN GEOGRAPHY + LOW-YIELD RELAXATION  — when an archetype's
    optimized count stays below the target band (a starved / thin-market / over-constrained
    persona), it is RE-OPTIMIZED with the geography widened categorically (city -> state/
    country -> nearby region — the jump the optimizer's radius expansion can't make), the
    industry filter dropped, and the language filter stripped. The widened condition is
    ADDED as a second archetype entry (base + widened, a union base-fetch + relaxed-refetch union),
    so a caller that fetches each archetype's condition
    collects the same candidates the batch does. Thin-market JDs (secondary cities,
    China-native, niche overseas roles) thus recover a real pool instead of near-nothing.
  • YIELD-AWARE archetype ordering in the returned ``archetypes`` detail (most-productive
    first), so a caller that wants only the Top-N archetypes gets the productive ones.


Two archetype-derivation modes:

  • JD-DRIVEN (preferred) — when ``single_process(..., job_desc=<JD text>)`` is given,
    derive 3-5 GENUINELY DISTINCT archetypes from the JD TEXT (ported from
    search_optimization_v3.6/archetype_pipeline.py + experiments/advanced_search_optimization_v3_deprecated.py):
    generate personas from the JD, then for EACH persona LLM-extract + typeahead-verify
    its OWN search fields (different titles / industries / skills / seniority / year range,
    dropping the industry filter for cross-industry personas). Each persona therefore
    targets a DIFFERENT candidate slice, so the per-archetype searches do NOT collapse
    back to one (which is what the old conditions-only mode below suffers from).

  • CONDITIONS-ONLY (fallback) — when no job_desc is given, derive archetypes by promoting
    each alternative job_title / industry from relaxation_options to PRIMARY. NOTE: the optimizer's relaxation tends to re-merge these into one search,
    so this mode adds little diversity — pass job_desc for real multi-archetype reach.

Either way: run the ORIGINAL ``single_process`` for EACH archetype, merge every
archetype's optimized ``final_conditions`` + ``final_skills`` into one union, count that
union once, and return the SAME output dict the original returns — plus an additive
``archetypes`` detail key (now INCLUDING per-archetype ``format_filter_conditions`` so a
caller can search each archetype separately).

Tuning (env or edit the constants):
    ASO_V3_MAX_PER_ARCHETYPE   per-archetype candidate cap (default 500)
    ASO_V3_MAX_ARCHETYPES      max number of archetypes incl. baseline (default 5)
    ASO_V3_FIELD_MODEL         LLM model for JD-driven field extraction (default gpt-4.1)
"""
import copy
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# ===========================================================================
# BASE OPTIMIZER (copied verbatim from src/advanced_search_optimization.py so this
# module is SELF-CONTAINED — no import from advanced_search_optimization). Excludes
# only the unused parallel-experiment variants, cache-stats helpers and __main__.
# ===========================================================================
import json
import logging
import os
import sys
import time
import uuid
import hashlib
from pathlib import Path

import ast
import pandas as pd

# Local dependencies: PREFER the graphs_v2 copies when they exist (so this module pairs with
# the graphs_v2 pipeline), falling back to the shared src-level modules otherwise.
try:
    from graphs_v2.config.linkedin_enums import get_linkedin_enum_data
except ImportError:
    from config.linkedin_enums import get_linkedin_enum_data
try:
    from graphs_v2.linkedin_recruiter_apiservice.api_service import RecruiterService
except ImportError:
    from linkedin_recruiter_apiservice.api_service import RecruiterService
try:
    from graphs_v2.utils.recruiter_api_formatter import (
        convert_filters_to_recruiter_api_conditions,
        clear_standardization_caches,
        get_standardization_cache_stats
    )
except ImportError:
    from utils.recruiter_api_formatter import (
        convert_filters_to_recruiter_api_conditions,
        clear_standardization_caches,
        get_standardization_cache_stats
    )
try:
    from graphs_v2.jd_smart_interactive_search_process import choose_job_main_skills
except ImportError:
    from jd_smart_interactive_search_process import choose_job_main_skills
try:
    from graphs_v2.llms.chatgpt import ChatGPTWrapper
except ImportError:
    from llms.chatgpt import ChatGPTWrapper

from typing import TypedDict, Literal, Optional, Dict, Any, List, Tuple
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from graphs_v2.utils.search_utils import expand_search_min_max_years
except ImportError:
    from utils.search_utils import expand_search_min_max_years
try:
    from graphs_v2.utils.general_utils import extract_json_block, clean_text
except ImportError:
    from utils.general_utils import extract_json_block, clean_text
try:
    from graphs_v2.utils.synonym_association import Synonym_Associations, get_synonym_associations_async
except ImportError:
    from utils.synonym_association import Synonym_Associations, get_synonym_associations_async
import asyncio


logger = logging.getLogger(__name__)


# ============================================================================
# CACHING MECHANISMS FOR PERFORMANCE OPTIMIZATION
# ============================================================================

# Cache for search results - key: (filters_hash, skills_tuple) -> result
_search_results_cache: Dict[str, Dict] = {}

# Cache for converted conditions - key: (filters_hash, skills_tuple) -> recruiter_conditions
_conditions_cache: Dict[str, Dict] = {}

# Global LLM instance to avoid repeated instantiation
_global_llm: Optional[ChatGPTWrapper] = None

# Global LinkedIn enum params cache
_linkedin_enum_params_cache: Optional[Dict] = None


def clear_optimization_caches():
    """Clear all optimization caches. Call this between different job searches."""
    global _search_results_cache, _conditions_cache
    _search_results_cache.clear()
    _conditions_cache.clear()
    # Also clear standardization caches
    clear_standardization_caches()
    print("✓ All optimization caches cleared")


def get_cache_stats() -> Dict[str, int]:
    """Get statistics about cache usage."""
    standardization_stats = get_standardization_cache_stats()
    return {
        "search_results_cache_size": len(_search_results_cache),
        "conditions_cache_size": len(_conditions_cache),
        **standardization_stats,
    }


def get_global_llm() -> ChatGPTWrapper:
    """Get or create a global LLM instance to avoid repeated instantiation."""
    global _global_llm
    if _global_llm is None:
        _global_llm = ChatGPTWrapper()
    return _global_llm


def get_cached_linkedin_enum_params() -> Dict:
    """Get or cache LinkedIn enum parameters."""
    global _linkedin_enum_params_cache
    if _linkedin_enum_params_cache is None:
        _linkedin_enum_params_cache = get_linkedin_enum_data()
    return _linkedin_enum_params_cache


def _make_cache_key(filters: dict, skills_list: list) -> str:
    """Create a hashable cache key from filters and skills."""
    try:
        filters_str = json.dumps(filters, sort_keys=True, default=str)
        skills_str = json.dumps(sorted(skills_list) if skills_list else [], default=str)
        combined = f"{filters_str}|{skills_str}"
        return hashlib.md5(combined.encode()).hexdigest()
    except Exception:
        # Fallback to string representation
        return hashlib.md5(f"{str(filters)}|{str(skills_list)}".encode()).hexdigest()


class SearchState(TypedDict):
    """State for search optimization workflow"""
    current_conditions: dict
    mandatory_skills: dict
    relaxation_options: dict
    current_count: int
    format_filter_conditions: str
    job_skills_str: str
    user_response: str
    relaxation_history: list  # Only tracks added conditions
    optimization_path_history: list  # Tracks all changes (additions and reductions)
    next_action: str
    skills_used_count: int  # Track number of skills being used
    auto_mode: bool  # Track if we're in automatic mode
    auto_relaxation_state: dict  # Track state for automatic relaxation
    min_target: int  # Minimum target for result count (default: 200)
    max_target: int  # Maximum target for result count (default: 600)
    total_input_tokens: int
    total_output_tokens: int
    # When False, the optimizer will NOT drop the industry filter to gain more
    # candidates (use for JDs where cross-industry candidates score poorly).
    allow_remove_industry: bool


def map_seniority_to_year_range(seniority_name: str, year_of_experience_options: list) -> dict:
    """
    Map a single seniority name to its year range dictionary.

    Args:
        seniority_name: Name of the seniority level (e.g., "senior", "entry")
        year_of_experience_options: List of [name, probability, year_range] tuples

    Returns:
        Year range dictionary with start_num_year and end_num_year
    """
    # Normalize the seniority name for matching
    normalized_seniority = seniority_name.lower().strip()

    for option in year_of_experience_options:
        if len(option) >= 3:
            option_name = option[0].lower().strip()
            # Handle variations like "entry" vs "entry level"
            if normalized_seniority in option_name or option_name in normalized_seniority:
                return option[2]  # Return the year range dictionary

    # If no exact match found, return None
    return None


def combine_seniority_year_ranges(seniority_list: list, year_of_experience_options: list) -> dict:
    """
    Combine multiple seniority values into a single year_of_experience range.
    Takes the minimum start_num_year and maximum end_num_year.

    Args:
        seniority_list: List of seniority names (e.g., ["entry", "senior"])
        year_of_experience_options: List of [name, probability, year_range] tuples

    Returns:
        Combined year range dictionary with min start and max end
    """
    if not seniority_list or not year_of_experience_options:
        return None

    min_start = None
    max_end = None
    search_min_start = None
    search_max_end = None

    for seniority in seniority_list:
        year_range = map_seniority_to_year_range(seniority, year_of_experience_options)
        if year_range:

            start = year_range.get('start_num_year', 0)
            end = year_range.get('end_num_year', 0)

            if min_start is None or start < min_start:
                min_start = start
            if max_end is None or end > max_end:
                max_end = end

            # Automatically expand search year range
            search_min_start, search_max_end = expand_search_min_max_years(min_start, max_end)

    if min_start is not None and max_end is not None:
        return {
            'start_num_year': search_min_start,
            'end_num_year': search_max_end,
            # "min_years": min_start,
            # "max_years": max_end,
        }

    return None


def get_priority_order():
    """Define the priority order for automatic relaxation"""
    return ["skills", "location", "seniority", "job_title", "job_function", "industry"]


def get_tightening_priority_order():
    """Define the priority order for tightening conditions (adding filters to reduce results)

    When initial conditions are empty/minimal and results exceed max_target,
    we add conditions in this order (highest priority first):
    - industry: Most specific business context
    - job_function: Broad category of role
    - job_title: Specific role names
    - seniority: Experience level
    - location: Geographic constraint
    - skills: Skill requirements (handled separately)
    """
    return ["industry", "job_function", "job_title", "seniority", "location"]


def check_and_expand_location(llm, location: str) -> dict:
    """Use a single LLM call to check if a location is a Metropolitan Area AND expand if not.

    Step 1: Determine if the location is a Metropolitan/Greater Area.
    Step 2: If NOT a Metropolitan Area, list nearby cities within 50, 100, 200 miles.

    Args:
        llm: ChatGPTWrapper instance
        location: Location string to check and potentially expand

    Returns:
        {
            "is_metro": bool,
            "within_50": [...],  # empty if is_metro=True
            "within_100": [...],
            "within_200": [...]
        }
    """
    prompt = f'''
    You have two tasks for the given location.

    **Task 1: Metropolitan Area Check**
    Determine if the location is a Metropolitan Area or Greater Area (a large geographic region covering multiple cities/counties).
    - Metropolitan Area examples: "San Francisco Bay Area", "Greater Seattle Area", "New York City Metropolitan Area", "Los Angeles Metropolitan Area", "Greater Boston", "Greater Chicago Area"
    - NOT Metropolitan Area examples: "Marshall, Michigan", "Canyon Village, WY", "Palo Alto, CA", "Austin, TX"
    A Metropolitan Area is a large region that already covers many cities. A single city, town, or small area is NOT a metropolitan area, even if it is a large city.
    
    **Task 2: Nearby City Expansion (only if NOT a Metropolitan Area)**
    If the location is NOT a Metropolitan Area, list nearby cities within 50, 100, and 200 miles.
    - Each sublist should contain city names formatted as "City, State_Full_Name". For example "Palo Alto, California".
    - Suggested cities must belong to the same country as the Provided Location.
    - Only include cities that are real and well-known enough to appear on LinkedIn.
    - Keep each list to at most 20 cities, prioritizing larger/more prominent cities.
    - The sublists should NOT overlap — "within_100" should only contain cities between 50-100 miles, and "within_200" should only contain cities between 100-200 miles.
    
    Return a JSON object:
    - If Metropolitan Area: {{"is_metro": true, "within_50": [], "within_100": [], "within_200": []}}
    - If NOT Metropolitan Area: {{"is_metro": false, "within_50": ["City1, State", ...], "within_100": ["City2, State", ...], "within_200": ["City3, State", ...]}}
    
    Example input: "Canyon Village, Wyoming"
    Example output: {{"is_metro": false, "within_50": ["Bozeman, Montana", "West Yellowstone, Montana"], "within_100": ["Idaho Falls, Idaho", "Billings, Montana"], "within_200": ["Missoula, Montana", "Jackson, Wyoming", "Great Falls, Montana"]}}
    
    Example input: "San Francisco Bay Area"
    Example output: {{"is_metro": true, "within_50": [], "within_100": [], "within_200": []}}
    ---
    Provided Location: "{location}"'''

    response_str, in_tok, out_tok = llm.invoke(prompt, temperature=0.1)
    result = extract_json_block(response_str)
    return {
        "is_metro": result.get("is_metro", False),
        "within_50": result.get("within_50", []),
        "within_100": result.get("within_100", []),
        "within_200": result.get("within_200", []),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


def expand_locations_for_optimization(job_location_list: list, llm=None) -> dict:
    """Expand non-metropolitan locations into nearby city tiers for search relaxation.

    For each location in the list, uses a single LLM call to:
    1. Check if it's a Metropolitan Area — if yes, skip expansion
    2. If not, expand to nearby cities at 50mi, 100mi, 200mi

    Args:
        job_location_list: List of location strings from JD parsing
        llm: Optional ChatGPTWrapper instance (created if not provided)

    Returns:
        {
            "original": [original locations],
            "expanded_50": [all 50mi cities across all non-metro locations],
            "expanded_100": [all 100mi cities],
            "expanded_200": [all 200mi cities],
        }
    """
    if not job_location_list:
        return {"original": [], "expanded_50": [], "expanded_100": [], "expanded_200": []}

    if llm is None:
        llm = ChatGPTWrapper()

    print("\n  Location Expansion for Optimization:")
    print(f"    Input locations: {job_location_list}")

    expanded_50, expanded_100, expanded_200 = [], [], []
    location_input_tokens, location_output_tokens = 0, 0

    for location in job_location_list:
        if location.lower().strip() == "remote":
            print(f"    Skipping 'Remote' — no expansion needed")
            continue

        try:
            result = check_and_expand_location(llm, location)
            location_input_tokens  += result.get("input_tokens",  0)
            location_output_tokens += result.get("output_tokens", 0)
            if result["is_metro"]:
                print(f"    '{location}' is a Metropolitan Area — skipping expansion")
            else:
                print(f"    '{location}' expanded:")
                print(f"      50mi: {result['within_50']}")
                print(f"      100mi: {result['within_100']}")
                print(f"      200mi: {result['within_200']}")
                expanded_50.extend(result["within_50"])
                expanded_100.extend(result["within_100"])
                expanded_200.extend(result["within_200"])
        except Exception as e:
            print(f"    Warning: Failed to check/expand location '{location}': {e}")

    print(f"    Expansion complete: {len(expanded_50)} cities at 50mi, {len(expanded_100)} at 100mi, {len(expanded_200)} at 200mi")

    return {
        "original": job_location_list,
        "expanded_50": expanded_50,
        "expanded_100": expanded_100,
        "expanded_200": expanded_200,
        "input_tokens": location_input_tokens,
        "output_tokens": location_output_tokens,
    }


def resolve_expanded_location_synonyms(location_expansion: dict, max_concurrent: int = 5) -> dict:
    """Resolve correct LinkedIn synonyms for all expanded locations in parallel.

    Uses the typeahead API (via get_synonym_associations_async) to map each
    expanded city name to its canonical LinkedIn location name.

    Args:
        location_expansion: Dict with keys "original", "expanded_50", "expanded_100", "expanded_200"
        max_concurrent: Max parallel API calls (default 5)

    Returns:
        Updated location_expansion dict with synonym-resolved city names
    """
    all_expanded = (
        location_expansion.get("expanded_50", [])
        + location_expansion.get("expanded_100", [])
        + location_expansion.get("expanded_200", [])
    )
    if not all_expanded:
        return location_expansion

    print(f"\n  Resolving synonyms for {len(all_expanded)} expanded locations (parallel, max_concurrent={max_concurrent})...")

    clean_locations = [clean_text(loc) for loc in all_expanded]
    synonym_dict = asyncio.run(
        get_synonym_associations_async(
            Synonym_Associations.LOCATION, clean_locations, max_concurrent=max_concurrent
        )
    )

    # Build mapping: clean_text(original) -> verified synonym
    synonym_map = {}
    for raw_loc, synonym_list in synonym_dict.items():
        if synonym_list:
            synonym_map[raw_loc] = synonym_list[0].replace(",", "")
        else:
            synonym_map[raw_loc] = raw_loc

    def resolve_tier(tier_list):
        resolved = []
        seen = set()
        for loc in tier_list:
            verified = synonym_map.get(clean_text(loc), loc)
            if verified not in seen:
                seen.add(verified)
                resolved.append(verified)
        return resolved

    location_expansion["expanded_50"] = resolve_tier(location_expansion["expanded_50"])
    location_expansion["expanded_100"] = resolve_tier(location_expansion["expanded_100"])
    location_expansion["expanded_200"] = resolve_tier(location_expansion["expanded_200"])

    logger.info(f"    50mi synonyms: {location_expansion['expanded_50']}")
    logger.info(f"    100mi synonyms: {location_expansion['expanded_100']}")
    logger.info(f"    200mi synonyms: {location_expansion['expanded_200']}")

    return location_expansion


def get_highest_probability_option(field: str, relaxation_options: dict) -> tuple:
    """Get the highest probability option from relaxation_options for a given field

    Args:
        field: The field name (e.g., "industry", "job_function")
        relaxation_options: Dictionary containing options with probabilities

    Returns:
        Tuple of (option_value, probability) or (None, 0) if not found
    """
    if field not in relaxation_options or not relaxation_options[field]:
        return None, 0

    options = relaxation_options[field]

    # Handle seniority field specially (format: [name, probability, year_range])
    if field == "seniority":
        if options and len(options) > 0 and len(options[0]) >= 2:
            # Options are sorted by probability, first one is highest
            return options[0][0], options[0][1]  # (seniority_name, probability)
        return None, 0

    # For other fields, format is [[value, probability], ...]
    if options and len(options) > 0:
        # Assume options are sorted by probability descending, or find the max
        best_option = None
        best_prob = -1
        for opt in options:
            if isinstance(opt, (list, tuple)) and len(opt) >= 2:
                value, prob = opt[0], opt[1]
                if prob > best_prob:
                    best_prob = prob
                    best_option = value
        if best_option is not None:
            return best_option, best_prob

    return None, 0


def add_tightening_condition(field: str, option_value, current_conditions: dict,
                             relaxation_options: dict = None) -> dict:
    """Add a condition to tighten search results (opposite of relaxation)

    Args:
        field: Field name to add condition to
        option_value: Value to add to the field
        current_conditions: Current search conditions dict
        relaxation_options: Optional, needed for seniority to get year range

    Returns:
        Updated conditions dict
    """
    updated_conditions = current_conditions.copy()

    if field == "seniority":
        # For seniority, add to the seniority list and update year_of_experience
        current_seniority = updated_conditions.get('seniority', [])
        if not isinstance(current_seniority, list):
            current_seniority = [current_seniority] if current_seniority else []

        # Add the new seniority value
        if option_value not in current_seniority:
            current_seniority.append(option_value)
            updated_conditions['seniority'] = current_seniority

        # Update year_of_experience based on seniority
        if relaxation_options and 'seniority' in relaxation_options:
            year_options = relaxation_options['seniority']
            combined_range = combine_seniority_year_ranges(current_seniority, year_options)
            if combined_range:
                updated_conditions['year_of_experience'] = combined_range

    elif field == "location":
        # Handle location field special case
        current_values = updated_conditions.get(field, {}).get('name', [])
        if not isinstance(current_values, list):
            current_values = [current_values] if current_values else []

        if isinstance(option_value, list):
            new_values = current_values + [item for item in option_value if item not in current_values]
        else:
            if option_value not in current_values:
                new_values = current_values + [option_value]
            else:
                new_values = current_values

        updated_conditions[field] = {'name': new_values}

    else:
        # For other fields (industry, job_function, job_title)
        current_values = updated_conditions.get(field, [])
        if not isinstance(current_values, list):
            current_values = [current_values] if current_values else []

        if isinstance(option_value, list):
            new_values = current_values + [item for item in option_value if item not in current_values]
        else:
            if option_value not in current_values:
                new_values = current_values + [option_value]
            else:
                new_values = current_values

        updated_conditions[field] = new_values

    return updated_conditions


def can_add_tightening_condition(field: str, current_conditions: dict, relaxation_options: dict) -> bool:
    """Check if we can add a tightening condition for a given field

    This checks if:
    1. The field is currently empty in current_conditions
    2. There are options available in relaxation_options

    Args:
        field: Field name to check
        current_conditions: Current search conditions
        relaxation_options: Available options with probabilities

    Returns:
        True if we can add a condition for this field
    """
    if field not in relaxation_options or not relaxation_options[field]:
        return False

    # Check if field is empty in current conditions
    if field == "location":
        current_values = current_conditions.get(field, {}).get('name', [])
    else:
        current_values = current_conditions.get(field, [])

    if not isinstance(current_values, list):
        current_values = [current_values] if current_values else []

    # Can add if current field is empty
    return len(current_values) == 0


def is_within_target_range(count: int, min_target: int = 200, max_target: int = 600) -> bool:
    """Check if count is within the target range"""
    return min_target <= count <= max_target


def can_add_more_conditions(field: str, current_conditions: dict, relaxation_options: dict,
                            mandatory_skills: list, skills_used_count: int) -> bool:
    """Check if there are more conditions to add for a given field"""
    if field == "skills":
        # Check if there are more skills to add
        return skills_used_count < len(mandatory_skills)

    # Special handling for seniority (maps to year_of_experience)
    if field == "seniority":
        # Check if there are year_of_experience options available (stored under seniority key)
        year_options = relaxation_options.get('seniority', [])
        if not year_options:
            return False

        # Get current seniority list
        current_seniority = current_conditions.get('seniority', [])
        if not isinstance(current_seniority, list):
            current_seniority = [current_seniority] if current_seniority else []

        # Normalize current seniority for comparison
        current_seniority_normalized = [s.lower().strip() for s in current_seniority if s]

        # Check if there are seniority options not yet added
        for option in year_options:
            if len(option) >= 3:
                seniority_name = option[0]
                seniority_name_normalized = seniority_name.lower().strip() if seniority_name else ""
                # Check if this seniority is not yet in the list
                if seniority_name_normalized not in current_seniority_normalized:
                    return True
        return False

    # Special handling for year_of_experience (if used directly)
    if field == "year_of_experience":
        # Check if year_of_experience is already set (either directly or via seniority)
        current_yoe = current_conditions.get('year_of_experience')
        if current_yoe is not None:
            # year_of_experience is already set - cannot add more
            return False

        # Check if seniority field exists and would conflict
        current_seniority = current_conditions.get('seniority', [])
        if current_seniority and len(current_seniority) > 0:
            # Seniority is managing year_of_experience, don't allow direct modification
            return False

        # Check if there are year_of_experience options available
        if field not in relaxation_options or not relaxation_options[field]:
            return False

        # We can add year_of_experience
        return True

    # For other fields, check if there are options not yet applied
    if field not in relaxation_options:
        return False

    if field not in current_conditions:
        return False

    # Handle location field special case
    if field.lower().strip() == "location":
        current_values = current_conditions[field].get('name', [])
    else:
        current_values = current_conditions.get(field, [])

    if not isinstance(current_values, list):
        current_values = [current_values]

    try:
        # Check if there are options not yet applied
        for option, _ in relaxation_options[field]:
            if isinstance(option, list):
                if not all(item in current_values for item in option):
                    return True
            else:
                if option not in current_values:
                    return True
    except Exception as e:
        print(f"relaxation_options[field]: {relaxation_options[field]}")

    return False


def get_next_condition_to_add(field: str, current_conditions: dict, relaxation_options: dict,
                              mandatory_skills: list, skills_used_count: int):
    """Get the next condition to add for a given field"""
    if field == "skills":
        # Return the next skill to add
        if skills_used_count < len(mandatory_skills):
            job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=skills_used_count + 1)
            if len(job_required_main_skills_list) > skills_used_count:
                next_skill = job_required_main_skills_list[-1]
                # Check if the skill is valid (not None)
                if next_skill is not None:
                    return next_skill
                else:
                    print(f"  Warning: Skill at position {skills_used_count} is None, skipping")
        return None

    # Special handling for seniority (returns the name, not the year range)
    if field == "seniority":
        # Get year_of_experience options from seniority key in relaxation_options
        year_options = relaxation_options.get('seniority', [])
        if not year_options:
            return None

        # Get current seniority list
        current_seniority = current_conditions.get('seniority', [])
        if not isinstance(current_seniority, list):
            current_seniority = [current_seniority] if current_seniority else []

        # Find the first seniority not yet added (based on probability order)
        for option in year_options:
            if len(option) >= 3:
                seniority_name = option[0]
                # Check if this seniority is not yet in the list
                if not any(s and s.lower().strip() == seniority_name.lower().strip() for s in current_seniority):
                    return seniority_name  # Return the seniority name, not the year range

        return None

    # Special handling for year_of_experience
    if field == "year_of_experience":
        if field not in relaxation_options:
            return None

        current_yoe = current_conditions.get(field)

        # year_of_experience_options format: [['senior', 0.7, {'end_num_year': 7, 'start_num_year': 4}], ...]
        # Select next option based on probability
        for option in relaxation_options[field]:
            if len(option) >= 3:
                seniority_name = option[0]
                probability = option[1]
                yoe_dict = option[2]

                # If no year_of_experience is currently set, return the first option's year range
                if current_yoe is None:
                    return yoe_dict

                # Otherwise find a different year range
                if yoe_dict != current_yoe:
                    return yoe_dict

        return None

    # For other fields, find the next option not yet applied
    if field not in relaxation_options or field not in current_conditions:
        return None

    # Handle location field special case
    if field.lower().strip() == "location":
        current_values = current_conditions[field].get('name', [])
    else:
        current_values = current_conditions.get(field, [])

    if not isinstance(current_values, list):
        current_values = [current_values]

    for option, _ in relaxation_options[field]:
        if isinstance(option, list):
            if not all(item in current_values for item in option):
                return option
        else:
            if option not in current_values:
                return option

    return None


def apply_condition(field: str, option, current_conditions: dict, skills_used_count: int,
                    relaxation_options: dict = None) -> tuple[dict, int]:
    """Apply a condition and return updated conditions and skills count"""
    updated_conditions = current_conditions.copy()
    updated_skills_count = skills_used_count

    if field == "skills":
        # Just update the skills count
        updated_skills_count = skills_used_count + 1
    elif field == "seniority":
        # For seniority, add to the seniority list and update year_of_experience
        current_seniority = updated_conditions.get('seniority', [])
        if not isinstance(current_seniority, list):
            current_seniority = [current_seniority] if current_seniority else []

        # Add the new seniority value
        if option not in current_seniority:
            current_seniority.append(option)
            updated_conditions['seniority'] = current_seniority

        # Update year_of_experience based on combined seniority values
        # Use either passed relaxation_options or year_of_experience_options from relaxation_options
        year_options = None
        if relaxation_options:
            if 'year_of_experience' in relaxation_options:
                year_options = relaxation_options['year_of_experience']
            elif 'seniority' in relaxation_options:
                year_options = relaxation_options['seniority']

        if year_options:
            combined_range = combine_seniority_year_ranges(current_seniority, year_options)
            if combined_range:
                updated_conditions['year_of_experience'] = combined_range
    elif field == "year_of_experience":
        # For year_of_experience, replace the entire value with the new year range dictionary
        # option is expected to be a dictionary like {'end_num_year': 7, 'start_num_year': 4}
        updated_conditions[field] = option
    else:
        # Handle location field special case
        if field.lower().strip() == "location":
            current_values = updated_conditions[field].get('name', [])
        else:
            current_values = updated_conditions.get(field, [])

        if not isinstance(current_values, list):
            current_values = [current_values]

        if isinstance(option, list):
            new_values = current_values + [item for item in option if item not in current_values]
        else:
            new_values = current_values + [option]

        # Apply the updated values preserving structure
        if field.lower().strip() == "location":
            updated_conditions[field] = {'name': new_values}
        else:
            updated_conditions[field] = new_values

    return updated_conditions, updated_skills_count


def remove_last_condition(field: str, current_conditions: dict, relaxation_history: list,
                          skills_used_count: int, relaxation_options: dict = None) -> tuple[dict, int, list]:
    """Remove the last added condition for a field"""
    # Find the last addition for this field in history
    updated_conditions = current_conditions.copy()
    updated_skills_count = skills_used_count
    updated_history = relaxation_history.copy()

    # Special handling for seniority field
    if field == "seniority":
        # For seniority, remove the last added seniority value and recalculate year_of_experience
        current_seniority = updated_conditions.get('seniority', [])
        if not isinstance(current_seniority, list):
            current_seniority = [current_seniority] if current_seniority else []

        # Find and remove the last seniority added from history
        for i in range(len(updated_history) - 1, -1, -1):
            if updated_history[i]['field'] == 'seniority':
                removed_item = updated_history.pop(i)
                seniority_to_remove = removed_item.get('added') or removed_item.get('value')

                # Remove from current seniority list
                if seniority_to_remove in current_seniority:
                    current_seniority.remove(seniority_to_remove)

                # Update conditions
                if current_seniority:
                    updated_conditions['seniority'] = current_seniority
                    # Recalculate year_of_experience based on remaining seniority values
                    if relaxation_options and 'seniority' in relaxation_options:
                        year_options = relaxation_options['seniority']
                        combined_range = combine_seniority_year_ranges(current_seniority, year_options)
                        if combined_range:
                            updated_conditions['year_of_experience'] = combined_range
                else:
                    # No seniority left, remove both fields
                    updated_conditions.pop('seniority', None)
                    updated_conditions.pop('year_of_experience', None)
                break

        return updated_conditions, updated_skills_count, updated_history

    # Special handling for year_of_experience
    if field == "year_of_experience":
        # For year_of_experience, we need to revert to None or previous value from history
        # Check if there's a previous year_of_experience in history
        previous_yoe = None
        for i in range(len(updated_history) - 1, -1, -1):
            if updated_history[i]['field'] == 'year_of_experience':
                updated_history.pop(i)
                # Find if there's an even earlier year_of_experience
                for j in range(i - 1, -1, -1):
                    if updated_history[j]['field'] == 'year_of_experience':
                        previous_yoe = updated_history[j].get('added') or updated_history[j].get('value')
                        break
                break

        # Set year_of_experience to previous value or None
        updated_conditions['year_of_experience'] = previous_yoe
        return updated_conditions, updated_skills_count, updated_history

    # Handle both singular and plural field names for job_function/job_functions
    actual_field = field
    if field in ["job_function", "job_functions"]:
        # Check which version exists in current_conditions
        if "job_function" in updated_conditions:
            actual_field = "job_function"
        elif "job_functions" in updated_conditions:
            actual_field = "job_functions"
        else:
            # Field doesn't exist, use the provided name
            actual_field = field

    # Find and remove the last condition added for this field
    for i in range(len(updated_history) - 1, -1, -1):
        if updated_history[i]['field'] == field:
            removed_item = updated_history.pop(i)

            if field == "skills":
                # Reduce skills count (but keep minimum of 2)
                updated_skills_count = max(2, skills_used_count - 1)
            else:
                # Remove the condition from the field (use actual_field for lookup)
                if actual_field in updated_conditions:
                    # Handle location field special case (use actual_field for lookup)
                    if actual_field.lower().strip() == "location":
                        current_values = updated_conditions[actual_field].get('name', [])
                    else:
                        current_values = updated_conditions.get(actual_field, [])

                    if not isinstance(current_values, list):
                        current_values = [current_values]

                    # Check if this field has more than 1 value before removing
                    if len(current_values) <= 1:
                        print(f"  Warning: Cannot remove from {field} - only 1 value remaining (minimum required)")
                        # Don't remove the last item, return original conditions
                        return current_conditions, skills_used_count, relaxation_history

                    # Remove the added option
                    option_to_remove = removed_item['added']
                    if isinstance(option_to_remove, list):
                        new_values = [v for v in current_values if v not in option_to_remove]
                    else:
                        new_values = [v for v in current_values if v != option_to_remove]

                    # Final safety check - ensure we're not creating an empty field
                    if len(new_values) == 0:
                        print(f"  Warning: Removing {option_to_remove} would leave {field} empty!")
                        print(f"  Keeping at least one value to maintain valid search conditions")
                        # Don't remove, return original conditions
                        return current_conditions, skills_used_count, relaxation_history

                    # Apply the updated values preserving structure (use actual_field)
                    if actual_field.lower().strip() == "location":
                        updated_conditions[actual_field] = {'name': new_values}
                    else:
                        updated_conditions[actual_field] = new_values
            break

    return updated_conditions, updated_skills_count, updated_history


def check_search_conditions(recruiter_conditions: dict) -> dict:
    """Remove keys under `filters` whose value is an empty list.

    Empty filter arrays (e.g. `job_functions: []`) can be mis-interpreted
    by the Recruiter API. Strip them so only populated filters are sent.
    Mutates and returns `recruiter_conditions`.
    """
    filters = recruiter_conditions.get("filters") if isinstance(recruiter_conditions, dict) else None
    if not isinstance(filters, dict):
        return recruiter_conditions
    empty_keys = [k for k, v in filters.items() if isinstance(v, list) and len(v) == 0]
    for k in empty_keys:
        del filters[k]
    if empty_keys:
        print(f"[search conditions] Removed empty-list keys: {empty_keys}")
    return recruiter_conditions


# Counter of LinkedIn count-probe API calls with DISTINCT search conditions (the optimizer tries
# many filter variants; caching means only distinct conditions reach the API). Thread-safe because
# archetypes optimize concurrently. reset at the start of a pipeline run, read at the end.
_LINKEDIN_COUNT_CALLS = 0
_LINKEDIN_COUNT_LOCK = __import__("threading").Lock()


def _count_linkedin_call():
    global _LINKEDIN_COUNT_CALLS
    with _LINKEDIN_COUNT_LOCK:
        _LINKEDIN_COUNT_CALLS += 1


def reset_linkedin_call_count():
    global _LINKEDIN_COUNT_CALLS
    with _LINKEDIN_COUNT_LOCK:
        _LINKEDIN_COUNT_CALLS = 0


def get_linkedin_call_count() -> int:
    return _LINKEDIN_COUNT_CALLS


def get_linkedin_search_num_cached(raw_filters, skills_list, llm=None):
    """
    Cached version of get_linkedin_search_num.
    Checks cache first before making API calls.
    """
    global _search_results_cache, _conditions_cache

    # Use global LLM if not provided
    if llm is None:
        llm = get_global_llm()

    # Create cache key
    cache_key = _make_cache_key(raw_filters, skills_list)

    # Check search results cache
    if cache_key in _search_results_cache:
        print(f"[CACHE HIT] Search results for this filter/skills combination")
        return _search_results_cache[cache_key]

    # Use cached linkedin_enum_params
    linkedin_enum_params = get_cached_linkedin_enum_params()

    # Check conditions cache
    if cache_key in _conditions_cache:
        print(f"[CACHE HIT] Recruiter conditions")
        recruiter_conditions = _conditions_cache[cache_key]
    else:
        # Convert filters to recruiter conditions
        try:
            recruiter_conditions = convert_filters_to_recruiter_api_conditions(
                raw_filters, skills_list, linkedin_enum_params, llm
            )
            # Cache the conditions
            _conditions_cache[cache_key] = recruiter_conditions
            print(f"[CACHE MISS] Converted and cached recruiter conditions")
        except Exception as e:
            import traceback
            print("[recruiter] convert_filters_to_recruiter_api_conditions failed:", e)
            print("[recruiter] traceback:\n", traceback.format_exc())
            raise

    input_tokens = recruiter_conditions.get('input_tokens') or 0
    output_tokens = recruiter_conditions.get('output_tokens') or 0

    # Drop empty-list filters (e.g. job_functions: []) before hitting the API.
    recruiter_conditions = check_search_conditions(recruiter_conditions)

    # Make API call
    rs = RecruiterService()
    try:
        api_start_time = time.time()
        print(f"Search condition: {recruiter_conditions}")
        _count_linkedin_call()   # distinct-condition count probe (cache misses only reach here)
        search_results = rs.get_search_num(recruiter_conditions)
        print(f"[API] get_search_num took {time.time() - api_start_time:.2f}s")
    except Exception as e:
        import traceback
        print("[recruiter] get search results via recruiter API failed:", e)
        print("[recruiter] traceback:\n", traceback.format_exc())
        raise

    search_results_num = search_results['num']

    output_dict = {
        "search_results_num": search_results_num,
        "format_filter_conditions": recruiter_conditions,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }

    # Cache the result
    _search_results_cache[cache_key] = output_dict
    print(f"[CACHE] Stored search result: {search_results_num} candidates")

    return output_dict


def get_linkedin_search_num(raw_filters, skills_list, llm=None):
    """
    Get LinkedIn search result count.
    This is a wrapper that uses the cached version for performance.
    """
    return get_linkedin_search_num_cached(raw_filters, skills_list, llm)


# def build_filters(conditions: dict) -> list:
#     """Build LinkedIn filter list from conditions"""
#     filters = []
#
#     if "job_function" in conditions:
#         filters.append({
#             "type": "FUNCTION",
#             "values": [{"text": f, "selectionType": "INCLUDED"} for f in conditions["job_function"]]
#         })
#
#     if "job_title" in conditions:
#         filters.append({
#             "type": "CURRENT_TITLE",
#             "values": [{"text": t, "selectionType": "INCLUDED"} for t in conditions["job_title"]]
#         })
#
#     if "seniority" in conditions:
#         filters.append({
#             "type": "SENIORITY_LEVEL",
#             "values": [{"text": s, "selectionType": "INCLUDED"} for s in conditions["seniority"]]
#         })
#
#     if "industry" in conditions:
#         filters.append({
#             "type": "INDUSTRY",
#             "values": [{"text": i, "selectionType": "INCLUDED"} for i in conditions["industry"]]
#         })
#
#     if "job_location" in conditions:
#         locations = conditions["job_location"] if isinstance(conditions["job_location"], list) else [conditions["job_location"]]
#         filters.append({
#             "type": "REGION",
#             "values": [{"text": loc, "selectionType": "INCLUDED"} for loc in locations]
#         })
#
#     return filters

#
# def perform_search(conditions: dict, job_skills: list = None) -> tuple[int, str, str]:
#     """Execute search and return count, filter string, and skills string"""
#     filters = build_filters(conditions)
#     format_filter_conditions = multi_filters_to_str(filters)
#     job_skills_str = build_skills_str(job_skills or conditions.get("job_skills", []))
#
#     search_results = search_linkedin(format_filter_conditions, job_skills_str)
#     count = search_results["data"]["paging"]["total"]
#
#     return count, format_filter_conditions, job_skills_str


# def perform_search(conditions: dict, job_skills: list = None) -> tuple[int, str, str]:
#     """Execute search and return count, filter string, and skills string"""
#     filters = build_filters(conditions)
#     format_filter_conditions = multi_filters_to_str(filters)
#     job_skills_str = build_skills_str(job_skills or conditions.get("job_skills", []))
#
#     search_results = search_linkedin(format_filter_conditions, job_skills_str)
#     count = search_results["data"]["paging"]["total"]
#
#     return count, format_filter_conditions, job_skills_str


def node_initial_search(state: SearchState) -> SearchState:
    """Node 1: Perform initial search"""
    print("\n=== Node 1: Initial Search ===")

    _node_in_tok = 0
    _node_out_tok = 0

    raw_filters = state["current_conditions"]
    mandatory_skills_list = state["mandatory_skills"]
    auto_mode = state.get("auto_mode", False)
    relaxation_options = state.get("relaxation_options", {})

    # Initialize current_conditions to track any reductions during this function
    current_conditions = raw_filters.copy()

    # Recalculate year_of_experience from seniority if seniority is present
    if 'seniority' in current_conditions and current_conditions['seniority']:
        seniority_list = current_conditions['seniority']
        if not isinstance(seniority_list, list):
            seniority_list = [seniority_list]

        # Get year_of_experience_options from relaxation_options
        year_of_experience_options = relaxation_options.get('seniority', [])

        if year_of_experience_options:
            # Combine seniority values to get year_of_experience range
            combined_year_range = combine_seniority_year_ranges(seniority_list, year_of_experience_options)
            if combined_year_range:
                current_conditions['year_of_experience'] = combined_year_range
                print(f"Recalculated year_of_experience from seniority {seniority_list}: {combined_year_range}")

    # Initialize skills_used_count at function level so it's accessible throughout
    skills_used_count = 2  # We start with 2 skills

    # count, filter_str, skills_str = perform_search(conditions)

    model_type = "gpt-4.1"
    temperature = 0.3
    max_tokens = 2048
    llm = ChatGPTWrapper()

    job_required_main_skills_list = choose_job_main_skills(mandatory_skills_list, pick_num=skills_used_count)

    search_results_info = get_linkedin_search_num(current_conditions,
                                                  job_required_main_skills_list,
                                                  llm)
    search_results_num = search_results_info['search_results_num']
    format_filter_conditions = search_results_info['format_filter_conditions']
    _node_in_tok += search_results_info.get('input_tokens', 0)
    _node_out_tok += search_results_info.get('output_tokens', 0)

    print(f"Search conditions: {raw_filters}")
    print(f"Found {search_results_num} results")
    print(f"Skills used: {job_required_main_skills_list}")

    # Initialize optimization path history (always track, regardless of mode)
    # Be extra defensive about initialization
    optimization_path_history = state.get("optimization_path_history", None)
    if optimization_path_history is None or not isinstance(optimization_path_history, list):
        optimization_path_history = []
        print(
            f"DEBUG [node_initial_search]: optimization_path_history was None or not a list, initialized as empty list")
    else:
        print(
            f"DEBUG [node_initial_search]: Retrieved optimization_path_history from state with {len(optimization_path_history)} entries")

    # Add initial state entry if this is the first search
    # Check more carefully for existing initial entry
    has_initial = False
    for step in optimization_path_history:
        if isinstance(step, dict) and step.get('action') == 'initial':
            has_initial = True
            break

    if not has_initial:
        print(f"DEBUG [node_initial_search]: No initial entry found, adding one...")
        optimization_path_history.append({
            "action": "initial",
            "field": "initial_search",
            "value": f"Started with {len(job_required_main_skills_list)} skills",
            "count_change": 0,
            "new_count": search_results_num
        })
        print(
            f"DEBUG [node_initial_search]: Added initial entry to optimization_path_history: {optimization_path_history[-1]}")
        print(
            f"DEBUG [node_initial_search]: optimization_path_history now has {len(optimization_path_history)} entries")
    else:
        print(f"DEBUG [node_initial_search]: Initial entry already exists, skipping")

    # Check if auto_mode is enabled
    if auto_mode:
        print("\n🤖 Auto-mode enabled - bypassing user interaction")

        # Get target range from state with defaults
        MIN_TARGET = state.get("min_target", 200)
        MAX_TARGET = state.get("max_target", 600)

        # Initialize auto_relaxation_state early to avoid reference errors
        auto_relaxation_state = state.get("auto_relaxation_state", {})

        # Check if results are already in target range
        if MIN_TARGET <= search_results_num <= MAX_TARGET:
            print(f"✓ Results ({search_results_num}) are already within target range ({MIN_TARGET}-{MAX_TARGET})")
            next_action = "official_search"
        elif search_results_num > MAX_TARGET:
            # Results exceed maximum - immediately start reduction following relaxation rules
            print(f"⚠️  Initial results ({search_results_num}) exceed maximum ({MAX_TARGET})")
            print("Starting immediate condition reduction using relaxation rules...")

            # Set current conditions same as initial conditions for reduction
            current_conditions = raw_filters.copy()
            current_count = search_results_num
            skills_used_count = len(job_required_main_skills_list)

            # Get priority order (same as relaxation but we'll reduce instead)
            priority_order = get_priority_order()

            # Continue reducing until within target or at minimum
            while current_count > MAX_TARGET:
                reduction_made = False

                # Try reducing each field in reverse priority order
                for field in reversed(priority_order):
                    if field == "skills":
                        # Try reducing the number of skills (keep minimum of 2)
                        if skills_used_count > 2:
                            print(f"  Reducing skills from {skills_used_count} to {skills_used_count - 1}...")

                            updated_skills_count = skills_used_count - 1
                            pick_num = min(updated_skills_count, len(mandatory_skills_list))
                            job_required_main_skills_list = choose_job_main_skills(mandatory_skills_list,
                                                                                   pick_num=pick_num)

                            # (
                            #     search_results,
                            #     reranked_job_titles,
                            #     format_filter_conditions,
                            #     job_main_skills,
                            #     input_tokens,
                            #     output_tokens,
                            #     sales_nav_filters,
                            # ) = main_linkedin_search_process(
                            #     current_conditions, job_required_main_skills_list, llm
                            # )
                            # new_count = search_results["data"]["paging"]["total"]
                            search_results_info = get_linkedin_search_num(current_conditions,
                                                                          job_required_main_skills_list,
                                                                          llm)

                            new_count = search_results_info['search_results_num']
                            format_filter_conditions = search_results_info['format_filter_conditions']
                            _node_in_tok += search_results_info.get('input_tokens', 0)
                            _node_out_tok += search_results_info.get('output_tokens', 0)

                            print(f"  After reducing skills: {new_count} results")

                            # Update optimization path history
                            optimization_path_history.append({
                                "action": "reduced_initial",
                                "field": "skills",
                                "value": f"from {skills_used_count} to {updated_skills_count}",
                                "count_change": new_count - current_count,
                                "new_count": new_count
                            })

                            current_count = new_count
                            skills_used_count = updated_skills_count
                            search_results_num = new_count

                            if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                                print(f"✓ Successfully reduced to target range!")
                                break

                            reduction_made = True
                            break  # Start next iteration from the beginning
                        else:
                            print(f"  Skills at minimum ({skills_used_count}), checking other conditions...")

                    elif field in current_conditions:
                        # Special handling for year_of_experience
                        if field == "year_of_experience":
                            # For year_of_experience, we need to remove it entirely or use probability-based selection
                            print(f"  Removing year_of_experience constraint...")

                            updated_conditions = current_conditions.copy()
                            # Remove year_of_experience entirely
                            del updated_conditions["year_of_experience"]

                            # Test with reduced conditions
                            pick_num = min(skills_used_count, len(mandatory_skills_list))
                            job_required_main_skills_list = choose_job_main_skills(mandatory_skills_list,
                                                                                   pick_num=pick_num)

                            search_results_info = get_linkedin_search_num(updated_conditions,
                                                                          job_required_main_skills_list,
                                                                          llm)

                            new_count = search_results_info['search_results_num']
                            format_filter_conditions = search_results_info['format_filter_conditions']
                            _node_in_tok += search_results_info.get('input_tokens', 0)
                            _node_out_tok += search_results_info.get('output_tokens', 0)

                            print(f"  After removing year_of_experience: {new_count} results")

                            # Get the value that was removed
                            removed_value = current_conditions["year_of_experience"]

                            # Check for drastic reduction
                            if current_count > MAX_TARGET and new_count < 100:
                                print(
                                    f"🔴 DRASTIC REDUCTION DETECTED during initial reduction: {current_count} → {new_count}")
                                print(f"  Field: year_of_experience, removed value: {removed_value}")
                                print("  This is a drastic drop. Will save state for potential rollback.")

                                # Save the state BEFORE this drastic reduction
                                auto_relaxation_state["drastic_reduction_detected"] = True
                                auto_relaxation_state["state_before_drastic_reduction"] = {
                                    "conditions": current_conditions.copy(),
                                    "skills_count": skills_used_count,
                                    "relaxation_history": state.get("relaxation_history", []).copy(),
                                    "optimization_path": optimization_path_history.copy(),
                                    "count": current_count,
                                    "path_length": len(optimization_path_history),
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None
                                }
                                auto_relaxation_state["recovery_attempts"] = 0
                                print(f"  Saved state with count={current_count} for potential rollback")

                            # Update optimization path history
                            optimization_path_history.append({
                                "action": "reduced_initial",
                                "field": "year_of_experience",
                                "value": removed_value,
                                "count_change": new_count - current_count,
                                "new_count": new_count
                            })

                            current_count = new_count
                            current_conditions = updated_conditions
                            search_results_num = new_count
                            raw_filters = updated_conditions  # Update the initial conditions

                            if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                                print(f"✓ Successfully reduced to target range!")
                                break

                            reduction_made = True
                            break  # Start next iteration from the beginning

                        # Try removing values from this field (for non-year_of_experience fields)
                        if field.lower().strip() == "location":
                            field_values = current_conditions[field].get('name', [])
                        else:
                            field_values = current_conditions.get(field, [])

                        if not isinstance(field_values, list):
                            field_values = [field_values]

                        # Only try to remove if there are multiple values
                        if len(field_values) > 1:
                            print(f"  Reducing {field} from {len(field_values)} to {len(field_values) - 1} values...")

                            # Remove the last value
                            updated_conditions = current_conditions.copy()
                            new_values = field_values[:-1]  # Remove last value

                            # Double-check that we're not creating an empty list
                            if len(new_values) == 0:
                                print(f"  Warning: Cannot remove last value from {field} - would leave field empty")
                                continue

                            if field.lower().strip() == "location":
                                updated_conditions[field] = {'name': new_values}
                            else:
                                updated_conditions[field] = new_values

                            # Test with reduced conditions
                            pick_num = min(skills_used_count, len(mandatory_skills_list))
                            job_required_main_skills_list = choose_job_main_skills(mandatory_skills_list,
                                                                                   pick_num=pick_num)

                            # (
                            #     search_results,
                            #     reranked_job_titles,
                            #     format_filter_conditions,
                            #     job_main_skills,
                            #     input_tokens,
                            #     output_tokens,
                            #     sales_nav_filters,
                            # ) = main_linkedin_search_process(
                            #     updated_conditions, job_required_main_skills_list, llm
                            # )
                            # new_count = search_results["data"]["paging"]["total"]

                            search_results_info = get_linkedin_search_num(updated_conditions,
                                                                          job_required_main_skills_list,
                                                                          llm)

                            new_count = search_results_info['search_results_num']
                            format_filter_conditions = search_results_info['format_filter_conditions']
                            _node_in_tok += search_results_info.get('input_tokens', 0)
                            _node_out_tok += search_results_info.get('output_tokens', 0)

                            print(f"  After reducing {field}: {new_count} results")

                            # Get the value that was removed
                            removed_value = field_values[-1]

                            # Check for drastic reduction (from > MAX_TARGET to < 100)
                            if current_count > MAX_TARGET and new_count < 100:
                                print(
                                    f"🔴 DRASTIC REDUCTION DETECTED during initial reduction: {current_count} → {new_count}")
                                print(f"  Field: {field}, removed value: {removed_value}")
                                print("  This is a drastic drop. Will save state for potential rollback.")

                                # Save the state BEFORE this drastic reduction
                                auto_relaxation_state["drastic_reduction_detected"] = True
                                auto_relaxation_state["state_before_drastic_reduction"] = {
                                    "conditions": current_conditions.copy(),
                                    "skills_count": skills_used_count,
                                    "relaxation_history": state.get("relaxation_history", []).copy(),
                                    "optimization_path": optimization_path_history.copy(),
                                    "count": current_count,
                                    "path_length": len(optimization_path_history),
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None  # job_main_skills #TODO fill in job_skills_str
                                }
                                auto_relaxation_state["recovery_attempts"] = 0
                                print(f"  Saved state with count={current_count} for potential rollback")

                            # Update optimization path history
                            optimization_path_history.append({
                                "action": "reduced_initial",
                                "field": field,
                                "value": removed_value,
                                "count_change": new_count - current_count,
                                "new_count": new_count
                            })

                            current_count = new_count
                            current_conditions = updated_conditions
                            search_results_num = new_count
                            raw_filters = updated_conditions  # Update the initial conditions

                            if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                                print(f"✓ Successfully reduced to target range!")
                                break

                            reduction_made = True
                            break  # Start next iteration from the beginning

                # Check if we're within range after inner loop
                if is_within_target_range(current_count, MIN_TARGET, MAX_TARGET):
                    break

                # Check if we can make any more reductions
                if not reduction_made:
                    # Check if all conditions are at minimum
                    at_minimum = True

                    # Check skills minimum
                    if skills_used_count > 2:
                        at_minimum = False

                    # Check other fields minimum
                    for field in current_conditions:
                        if field.lower().strip() == "location":
                            field_values = current_conditions[field].get('name', [])
                        else:
                            field_values = current_conditions.get(field, [])

                        if not isinstance(field_values, list):
                            field_values = [field_values]

                        if len(field_values) > 1:
                            at_minimum = False
                            break

                    if at_minimum:
                        print(f"\n⚠️  All conditions at minimum. Cannot reduce further.")
                        print(f"  Current result count: {current_count}")

                        # NEW: Try tightening by adding conditions from relaxation_options
                        # when initial conditions are empty and results still exceed max_target
                        if current_count > MAX_TARGET:
                            print(f"\n🔧 Attempting to tighten search by adding conditions from relaxation_options...")
                            print(f"  Target: reduce results from {current_count} to below {MAX_TARGET}")

                            tightening_priority = get_tightening_priority_order()
                            tightening_made = False

                            # Continue tightening until within target or no more options
                            while current_count > MAX_TARGET:
                                tightening_iteration_made = False

                                for tighten_field in tightening_priority:
                                    # Check if we can add a tightening condition for this field
                                    if can_add_tightening_condition(tighten_field, current_conditions, relaxation_options):
                                        # Get the highest probability option for this field
                                        option_value, probability = get_highest_probability_option(tighten_field, relaxation_options)

                                        if option_value is not None:
                                            print(f"\n  Adding {tighten_field}: '{option_value}' (probability: {probability:.2f})")

                                            # Apply the tightening condition
                                            updated_conditions = add_tightening_condition(
                                                tighten_field, option_value, current_conditions, relaxation_options
                                            )

                                            # Test with the tightened conditions
                                            pick_num = min(skills_used_count, len(mandatory_skills_list))
                                            job_required_main_skills_list = choose_job_main_skills(mandatory_skills_list, pick_num=pick_num)

                                            search_results_info = get_linkedin_search_num(updated_conditions,
                                                                                          job_required_main_skills_list,
                                                                                          llm)

                                            new_count = search_results_info['search_results_num']
                                            format_filter_conditions = search_results_info['format_filter_conditions']

                                            print(f"  After adding {tighten_field}: {new_count} results (reduced by {current_count - new_count})")

                                            # Update optimization path history
                                            optimization_path_history.append({
                                                "action": "tightened",
                                                "field": tighten_field,
                                                "value": option_value,
                                                "count_change": new_count - current_count,
                                                "new_count": new_count
                                            })

                                            current_count = new_count
                                            current_conditions = updated_conditions
                                            search_results_num = new_count
                                            tightening_made = True
                                            tightening_iteration_made = True

                                            if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                                                print(f"  ✓ Successfully tightened to target range!")
                                                break

                                            # Don't break - continue to next field in priority order
                                            # Only break inner loop if we're now within range
                                            break  # Move to next iteration of while loop

                                # Check if we're within range after inner loop
                                if is_within_target_range(current_count, MIN_TARGET, MAX_TARGET):
                                    break

                                # If no tightening was made in this iteration, we've exhausted options
                                if not tightening_iteration_made:
                                    print(f"\n  ⚠️  No more tightening options available.")
                                    print(f"  Final result count after tightening: {current_count}")
                                    break

                            if tightening_made:
                                print(f"\n📊 Tightening complete. Final count: {current_count}")

                        break

            print(f"\n📊 Reduction complete. Final count: {search_results_num}")

            # Check final state and decide next action
            if is_within_target_range(search_results_num, MIN_TARGET, MAX_TARGET):
                next_action = "official_search"
            else:
                next_action = "auto_relaxation"  # Try relaxation if still outside range

            # Initialize auto relaxation state if needed
            if next_action == "auto_relaxation":
                auto_relaxation_state = state.get("auto_relaxation_state", {})
                if not auto_relaxation_state:
                    auto_relaxation_state = {
                        "current_priority_index": 0,
                        "fields_exhausted": [],
                        "backtrack_attempts": {}
                    }
        else:
            # Results below minimum
            print(f"Results ({search_results_num}) below minimum target ({MIN_TARGET})")
            print("Starting automatic relaxation...")
            next_action = "auto_relaxation"

            # Initialize auto relaxation state if not already present
            auto_relaxation_state = state.get("auto_relaxation_state", {})
            if not auto_relaxation_state:
                auto_relaxation_state = {
                    "current_priority_index": 0,
                    "fields_exhausted": [],
                    "backtrack_attempts": {}
                }
    else:
        next_action = "ask_user"
        auto_relaxation_state = state.get("auto_relaxation_state", {})

    # Return the skills_used_count that was tracked throughout this function
    print(f"DEBUG [node_initial_search]: About to return state with optimization_path_history...")
    print(f"DEBUG [node_initial_search]: optimization_path_history has {len(optimization_path_history)} entries")
    if optimization_path_history:
        print(f"DEBUG [node_initial_search]: Full optimization_path_history being returned:")
        for i, entry in enumerate(optimization_path_history):
            print(f"  Entry {i}: {entry}")
    print(f"DEBUG [node_initial_search]: next_action = '{next_action}'")
    return {
        **state,
        "current_conditions": current_conditions,  # Return potentially reduced conditions
        "current_count": search_results_num,
        "format_filter_conditions": format_filter_conditions,
        "job_skills_str": None,  # job_main_skills, # TODO: Fill in job_skills_str
        "skills_used_count": skills_used_count,  # Use the local variable that was updated during reduction
        "optimization_path_history": optimization_path_history,  # Use the local variable built up in this function
        "auto_relaxation_state": auto_relaxation_state,  # Include the auto_relaxation_state
        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
        "next_action": next_action
    }


def node_ask_user_satisfaction(state: SearchState) -> SearchState:
    """Node 2: Check if results are acceptable"""
    print("\n=== Node 2: Check User Satisfaction ===")

    count = state["current_count"]
    print(f"Current search returned {count} results")

    if count < 200:
        print(f"⚠️  Result count ({count}) is below recommended minimum (200)")

    print("\nOptions:")
    print("1. Proceed with official search")
    print("2. Manually relax conditions (step-by-step)")
    print("3. Automatically relax conditions (based on priorities)")

    user_input = input("\nChoose an option (1/2/3): ").strip()

    if user_input in ['1', 'proceed', 'p']:
        return {**state, "user_response": "proceed", "next_action": "official_search"}
    elif user_input in ['2', 'manual', 'm']:
        return {**state, "user_response": "relax", "auto_mode": False, "next_action": "analyze_relaxation"}
    elif user_input in ['3', 'auto', 'a']:
        # Initialize automatic relaxation state
        auto_state = {
            "current_priority_index": 0,
            "fields_exhausted": [],
            "backtrack_attempts": {}
        }
        return {**state, "user_response": "auto", "auto_mode": True,
                "auto_relaxation_state": auto_state, "next_action": "auto_relaxation"}


def node_analyze_relaxation(state: SearchState) -> SearchState:
    """Node 3: Analyze and present relaxation options"""
    print("\n=== Node 3: Analyzing Relaxation Options ===")

    _node_in_tok = 0
    _node_out_tok = 0

    relaxation_options = state["relaxation_options"]
    current_conditions = state["current_conditions"]
    mandatory_skills = state["mandatory_skills"]
    current_count = state["current_count"]
    current_skills_used = state.get("skills_used_count", 0)

    model_type = "gpt-4.1"
    temperature = 0.3
    max_tokens = 2048
    llm = ChatGPTWrapper()

    stats = []

    # Test skill relaxation option first
    max_available_skills = len(mandatory_skills)
    if current_skills_used < max_available_skills:
        # Test adding one more skill
        new_skills_count = current_skills_used + 1
        try:
            job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=new_skills_count)
            next_skill = job_required_main_skills_list[-1] if len(
                job_required_main_skills_list) > current_skills_used else None

            if next_skill:
                # (
                #     search_results,
                #     reranked_job_titles,
                #     format_filter_conditions,
                #     job_main_skills,
                #     input_tokens,
                #     output_tokens,
                #     sales_nav_filters,
                # ) = main_linkedin_search_process(
                #     current_conditions, job_required_main_skills_list, llm
                # )
                # search_results_num = search_results["data"]["paging"]["total"]

                search_results_info = get_linkedin_search_num(current_conditions,
                                                              job_required_main_skills_list,
                                                              llm)

                search_results_num = search_results_info['search_results_num']
                format_filter_conditions = search_results_info['format_filter_conditions']
                _node_in_tok += search_results_info.get('input_tokens', 0)
                _node_out_tok += search_results_info.get('output_tokens', 0)

                increase = search_results_num - current_count

                # Get the skill probability
                skill_prob = 0.0
                for skill_dict in mandatory_skills:
                    if skill_dict.get("skill") == next_skill:
                        skill_prob = skill_dict.get("probability", 0.0)
                        break

                stats.append({
                    "field": "skills",
                    "option": next_skill,
                    "probability": skill_prob,
                    "current_count": current_count,
                    "new_count": search_results_num,
                    "increase": increase
                })
        except Exception as e:
            print(f"Error testing skill relaxation: {e}")

    # Test each relaxation option - find the NEXT item not yet applied
    for field, options in relaxation_options.items():
        if field not in current_conditions:
            continue

        if field.lower().strip() == "location":
            current_values = current_conditions[field]['name']
        else:
            current_values = current_conditions[field]
        if not isinstance(current_values, list):
            current_values = [current_values]

        # Find the next option not yet in current_values
        next_option = None
        next_probability = None

        for option, probability in options:

            # Check if this option is already applied
            if isinstance(option, list):
                # For list options, check if all items are already present
                if all(item in current_values for item in option):
                    continue
                else:
                    next_option = option
                    next_probability = probability
                    break
            else:
                # For single options, check if already present
                if option not in current_values:
                    next_option = option
                    next_probability = probability
                    break

        # If we found a next option, test it

        if next_option is not None:
            # Build new values
            if isinstance(next_option, list):
                new_values = current_values + [item for item in next_option if item not in current_values]
            else:
                new_values = current_values + [next_option]

            # Test relaxation
            test_conditions = current_conditions.copy()

            # Handle location field special case - preserve {'name': [...]} structure
            if field.lower().strip() == "location":
                test_conditions[field] = {'name': new_values}
            else:
                test_conditions[field] = new_values

            try:
                job_required_main_skills_list = choose_job_main_skills(mandatory_skills)
                # (
                #     search_results,
                #     reranked_job_titles,
                #     format_filter_conditions,
                #     job_main_skills,
                #     input_tokens,
                #     output_tokens,
                #     sales_nav_filters,
                # ) = main_linkedin_search_process(
                #     test_conditions, job_required_main_skills_list, llm
                # )
                # search_results_num = search_results["data"]["paging"]["total"]

                search_results_info = get_linkedin_search_num(test_conditions,
                                                              job_required_main_skills_list,
                                                              llm)

                search_results_num = search_results_info['search_results_num']
                format_filter_conditions = search_results_info['format_filter_conditions']
                _node_in_tok += search_results_info.get('input_tokens', 0)
                _node_out_tok += search_results_info.get('output_tokens', 0)

                increase = search_results_num - current_count

                stats.append({
                    "field": field,
                    "option": next_option,
                    "probability": next_probability,
                    "current_count": current_count,
                    "new_count": search_results_num,
                    "increase": increase
                })
            except Exception as e:
                print(f"Error testing {field} with {next_option}: {e}")

    if not stats:
        print("No relaxation options available.")
        return {**state, "user_response": "no_options", "next_action": "official_search",
                "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok}

    # Display statistics sorted by increase
    stats.sort(key=lambda x: x["increase"], reverse=True)

    print("\nRelaxation Options (sorted by result increase):")
    print("-" * 90)
    for idx, stat in enumerate(stats, 1):
        print(f"{idx}. Field: {stat['field']:<15} | Add: {str(stat['option']):<40}")
        print(
            f"   Probability: {stat['probability']:<6} | Results: {stat['current_count']:>5} → {stat['new_count']:>5} (+{stat['increase']:>5})")
        print()

    # User selection
    choice = input("Choose relaxation option (number) or 'skip': ").strip()

    if choice.lower() == 'skip':
        return {**state, "user_response": "skip", "next_action": "official_search",
                "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok}

    try:
        choice_idx = int(choice) - 1
        chosen = stats[choice_idx]

        # Apply relaxation
        field = chosen["field"]
        option = chosen["option"]

        updated_conditions = current_conditions.copy()
        updated_skills_count = current_skills_used

        # Handle skill relaxation
        if field == "skills":
            # Just increment the skills count - skills will be applied in node_check_further_relaxation
            updated_skills_count = current_skills_used + 1
        else:
            # Handle condition field relaxation
            # Handle location field special case
            if field.lower().strip() == "location":
                current_values = updated_conditions[field]['name']
            else:
                current_values = updated_conditions[field]

            if not isinstance(current_values, list):
                current_values = [current_values]

            if isinstance(option, list):
                new_values = current_values + [item for item in option if item not in current_values]
            else:
                new_values = current_values + [option]

            # Apply the updated values preserving structure
            if field.lower().strip() == "location":
                updated_conditions[field] = {'name': new_values}
            else:
                updated_conditions[field] = new_values

        relaxation_history = state.get("relaxation_history", [])
        relaxation_history.append({
            "field": field,
            "added": option,
            "count_increase": chosen["increase"]
        })

        # Update optimization path history as well
        optimization_path_history = state.get("optimization_path_history", [])
        optimization_path_history.append({
            "action": "added",
            "field": field,
            "value": option,
            "count_change": chosen["increase"],
            "new_count": chosen["new_count"]
        })

        return {
            **state,
            "current_conditions": updated_conditions,
            "relaxation_history": relaxation_history,
            "optimization_path_history": optimization_path_history,
            "user_response": "applied",
            "skills_used_count": updated_skills_count,
            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
            "next_action": "check_further_relaxation"
        }

    except (ValueError, IndexError):
        print("Invalid choice. Skipping relaxation.")
        return {**state, "user_response": "skip", "next_action": "official_search",
                "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok}


def node_check_further_relaxation(state: SearchState) -> SearchState:
    """Node 4: Check if user wants more relaxation"""
    print("\n=== Node 4: Check Further Relaxation ===")

    _node_in_tok = 0
    _node_out_tok = 0

    # Re-search with updated conditions using main_linkedin_search_process
    current_conditions = state["current_conditions"]
    mandatory_skills_list = state["mandatory_skills"]
    llm = ChatGPTWrapper()

    # Use the current skill count from state (may have been updated in node_analyze_relaxation)
    current_skills_used = state.get("skills_used_count", 0)

    # Use pick_num to get exactly current_skills_used skills
    # (but don't exceed the total available skills)
    max_available_skills = len(mandatory_skills_list)
    pick_num = min(current_skills_used, max_available_skills)

    job_required_main_skills_list = choose_job_main_skills(mandatory_skills_list, pick_num=pick_num)
    # (
    #     search_results,
    #     reranked_job_titles,
    #     filter_str,
    #     skills_str,
    #     input_tokens,
    #     output_tokens,
    #     sales_nav_filters,
    # ) = main_linkedin_search_process(
    #     conditions, job_required_main_skills_list, llm
    # )
    # count = search_results["data"]["paging"]["total"]

    search_results_info = get_linkedin_search_num(current_conditions,
                                                  job_required_main_skills_list,
                                                  llm)

    new_count = search_results_info['search_results_num']
    format_filter_conditions = search_results_info['format_filter_conditions']
    _node_in_tok += search_results_info.get('input_tokens', 0)
    _node_out_tok += search_results_info.get('output_tokens', 0)

    print(f"Updated search: {new_count} results")
    print(f"Skills used: {job_required_main_skills_list} (count: {len(job_required_main_skills_list)})")
    print(f"Relaxations applied: {len(state.get('relaxation_history', []))}")

    # Check for remaining options
    relaxation_options = state["relaxation_options"]
    has_more_options = False

    for field, options in relaxation_options.items():
        if field not in current_conditions:
            continue

        # Handle location field special case
        if field.lower().strip() == "location":
            current_values = current_conditions[field]['name']
        else:
            current_values = current_conditions[field]

        if not isinstance(current_values, list):
            current_values = [current_values]

        for option, _ in options:
            if isinstance(option, list):
                if not all(item in current_values for item in option):
                    has_more_options = True
                    break
            else:
                if option not in current_values:
                    has_more_options = True
                    break
        if has_more_options:
            break

    if not has_more_options:
        print("✓ No more relaxation options available")
        return {
            **state,
            "current_count": new_count,
            "format_filter_conditions": None,  # filter_str, # TODO: Fill in format_filter_conditions
            "job_skills_str": None,  # skills_str, # TODO: Fill in job_skills_str
            "skills_used_count": len(job_required_main_skills_list),
            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
            "next_action": "official_search"
        }

    if new_count >= 200:
        user_input = input(
            f"Search has sufficient results ({new_count}). Continue relaxing? (yes/no): ").strip().lower()
    else:
        user_input = input(f"Continue relaxing? ({new_count} results, recommended: 200+) (yes/no): ").strip().lower()

    if user_input in ['yes', 'y']:
        return {
            **state,
            "current_count": new_count,
            "format_filter_conditions": None,  # filter_str, TODO: Fill in format_filter_conditions
            "job_skills_str": None,  # skills_str, # TODO: Fill in job_skills_str
            "skills_used_count": len(job_required_main_skills_list),
            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
            "next_action": "analyze_relaxation"
        }
    else:
        return {
            **state,
            "current_count": new_count,
            "format_filter_conditions": None,  # filter_str, TODO
            "job_skills_str": None,  # skills_str, # TODO
            "skills_used_count": len(job_required_main_skills_list),
            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
            "next_action": "official_search"
        }


def node_auto_relaxation(state: SearchState) -> SearchState:
    """Node for automatic condition relaxation based on priorities"""
    print("\n=== Automatic Relaxation Mode ===")

    _node_in_tok = 0
    _node_out_tok = 0

    current_conditions = state["current_conditions"]
    mandatory_skills = state["mandatory_skills"]
    relaxation_options = state["relaxation_options"]
    current_count = state["current_count"]
    skills_used_count = state.get("skills_used_count", 0)
    relaxation_history = state.get("relaxation_history", [])

    # CRITICAL: Ensure optimization_path_history is properly initialized
    # Check if the key exists and has a valid list value
    optimization_path_history = state.get("optimization_path_history", None)
    if optimization_path_history is None:
        print(
            f"WARNING [node_auto_relaxation]: optimization_path_history is None in state! Initializing as empty list.")
        optimization_path_history = []
    elif not isinstance(optimization_path_history, list):
        print(
            f"WARNING [node_auto_relaxation]: optimization_path_history is not a list: {type(optimization_path_history)}. Initializing as empty list.")
        optimization_path_history = []
    else:
        pass  # optimization_path_history retrieved successfully

    auto_state = state.get("auto_relaxation_state", {})

    # Check for drastic reduction recovery mode
    if auto_state.get("drastic_reduction_detected", False):
        print("⚠️ DRASTIC REDUCTION RECOVERY MODE")
        if auto_state.get("recovery_failed", False):
            # Recovery has been attempted and failed, rollback to saved state
            saved_state = auto_state.get("state_before_drastic_reduction", {})
            if saved_state:
                print("🔄 ROLLING BACK to state before drastic reduction")
                print(f"  Restoring count: {saved_state.get('count', 'unknown')}")
                print(f"  Restoring optimization path to {saved_state.get('path_length', 0)} entries")

                # Add rollback entry to optimization path to show what happened
                rollback_optimization_path = optimization_path_history.copy()
                rollback_optimization_path.append({
                    "action": "rollback",
                    "field": "system",
                    "value": f"Rolled back to state before drastic reduction",
                    "count_change": saved_state.get("count", current_count) - current_count,
                    "new_count": saved_state.get("count", current_count)
                })

                # Clear the drastic reduction flags
                auto_state_cleared = auto_state.copy()
                auto_state_cleared.pop("drastic_reduction_detected", None)
                auto_state_cleared.pop("state_before_drastic_reduction", None)
                auto_state_cleared.pop("recovery_failed", None)

                return {
                    **state,
                    "current_conditions": saved_state.get("conditions", current_conditions),
                    "skills_used_count": saved_state.get("skills_count", skills_used_count),
                    "relaxation_history": saved_state.get("relaxation_history", relaxation_history),
                    "optimization_path_history": rollback_optimization_path,  # Use the path with rollback entry added
                    "current_count": saved_state.get("count", current_count),
                    "format_filter_conditions": saved_state.get("format_filter_conditions",
                                                                state.get("format_filter_conditions", "")),
                    "job_skills_str": saved_state.get("job_skills_str", state.get("job_skills_str", "")),
                    "auto_relaxation_state": auto_state_cleared,
                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                    "next_action": "official_search"  # Accept the pre-reduction state as final
                }

    print(
        f"DEBUG [node_auto_relaxation]: Entry point - optimization_path_history has {len(optimization_path_history)} entries")
    if optimization_path_history:
        print(f"DEBUG [node_auto_relaxation]: Entries in optimization_path_history:")
        for i, entry in enumerate(optimization_path_history):
            print(
                f"  Entry {i}: action={entry.get('action')}, field={entry.get('field')}, new_count={entry.get('new_count')}")

    llm = ChatGPTWrapper()

    # Get target range from state with defaults
    MIN_TARGET = state.get("min_target", 200)
    MAX_TARGET = state.get("max_target", 600)

    print(f"Current results: {current_count}")
    print(f"Target range: {MIN_TARGET} - {MAX_TARGET}")

    # Check if we're already in the target range
    if is_within_target_range(current_count, MIN_TARGET, MAX_TARGET):
        print(f"✓ Results ({current_count}) are within target range!")
        return {
            **state,
            "optimization_path_history": optimization_path_history,  # Ensure history is included
            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
            "next_action": "official_search"
        }

    # If we're above the maximum, need to reduce conditions
    if current_count > MAX_TARGET:
        print(f"⚠️  Results ({current_count}) exceed maximum. Attempting to reduce conditions...")

        # Check if we have any relaxation history to remove from
        if relaxation_history:
            # Find the least important field that was added
            priority_order = get_priority_order()

            # Reverse priority for removal (remove least important first)
            for field in reversed(priority_order):
                # Check if this field has been relaxed in history
                field_relaxations = [h for h in relaxation_history if h['field'] == field]

                if field_relaxations:
                    print(f"Attempting to remove last addition from {field}...")

                    # Remove the last condition for this field
                    updated_conditions, updated_skills_count, updated_history = remove_last_condition(
                        field, current_conditions, relaxation_history, skills_used_count
                    )

                    # Test the removal
                    pick_num = min(updated_skills_count, len(mandatory_skills))
                    job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=pick_num)

                    # (
                    #     search_results,
                    #     reranked_job_titles,
                    #     format_filter_conditions,
                    #     job_main_skills,
                    #     input_tokens,
                    #     output_tokens,
                    #     sales_nav_filters,
                    # ) = main_linkedin_search_process(
                    #     updated_conditions, job_required_main_skills_list, llm
                    # )
                    # new_count = search_results["data"]["paging"]["total"]

                    search_results_info = get_linkedin_search_num(updated_conditions,
                                                                  job_required_main_skills_list,
                                                                  llm)

                    new_count = search_results_info['search_results_num']
                    format_filter_conditions = search_results_info['format_filter_conditions']
                    _node_in_tok += search_results_info.get('input_tokens', 0)
                    _node_out_tok += search_results_info.get('output_tokens', 0)

                    print(f"After removing from {field}: {new_count} results")

                    # Get the removed item to track in optimization path
                    removed_item = None
                    for h in reversed(relaxation_history):
                        if h['field'] == field:
                            removed_item = h
                            break

                    # Update optimization path history to track the reduction
                    updated_optimization_path = optimization_path_history.copy()
                    if removed_item:
                        updated_optimization_path.append({
                            "action": "removed",
                            "field": field,
                            "value": removed_item['added'],
                            "count_change": new_count - current_count,
                            "new_count": new_count
                        })

                    if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                        print(f"✓ Successfully reduced to target range!")
                        return {
                            **state,
                            "current_conditions": updated_conditions,
                            "skills_used_count": updated_skills_count,
                            "relaxation_history": updated_history,
                            "optimization_path_history": updated_optimization_path,
                            "current_count": new_count,
                            "format_filter_conditions": format_filter_conditions,
                            "job_skills_str": None,  # job_main_skills, #TODO
                            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                            "next_action": "official_search"
                        }

                    # Check if we've gone too low after removal (below 100)
                    if new_count < 100:
                        # Check if this is a drastic reduction (from > MAX_TARGET to < 100)
                        if current_count > MAX_TARGET:
                            print(f"🔴 DRASTIC REDUCTION DETECTED: {current_count} → {new_count}")
                            print("Saving state before reduction and attempting recovery...")

                            # Save the state before this drastic reduction
                            auto_state["drastic_reduction_detected"] = True
                            auto_state["state_before_drastic_reduction"] = {
                                "conditions": current_conditions.copy(),
                                "skills_count": skills_used_count,
                                "relaxation_history": relaxation_history.copy(),
                                "optimization_path": optimization_path_history.copy(),
                                "count": current_count,
                                "path_length": len(optimization_path_history),
                                "format_filter_conditions": state.get("format_filter_conditions", ""),
                                "job_skills_str": state.get("job_skills_str", "")
                            }
                            auto_state["recovery_attempts"] = 0  # Initialize recovery counter

                            # Continue with the reduction but switch to recovery mode
                            return {
                                **state,
                                "current_conditions": updated_conditions,
                                "skills_used_count": updated_skills_count,
                                "relaxation_history": updated_history,
                                "optimization_path_history": updated_optimization_path,
                                "current_count": new_count,
                                "format_filter_conditions": format_filter_conditions,
                                "job_skills_str": None,  # job_main_skills, #TODO
                                "auto_relaxation_state": auto_state,
                                "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                "next_action": "auto_relaxation"  # Continue to try recovery
                            }
                        else:
                            # Normal case: just undo the reduction
                            print(f"⚠️ Removal caused results to drop too low ({new_count} < 100).")
                            print("Undoing last reduction and accepting previous state.")
                            if updated_optimization_path and updated_optimization_path[-1].get('action') == 'removed':
                                updated_optimization_path.pop()
                            return {
                                **state,
                                "current_conditions": current_conditions,  # Keep original conditions
                                "skills_used_count": skills_used_count,  # Keep original skills count
                                "relaxation_history": relaxation_history,  # Keep original history
                                "optimization_path_history": optimization_path_history,  # Keep original path
                                "current_count": current_count,  # Keep original count (before removal)
                                "format_filter_conditions": state.get("format_filter_conditions", ""),
                                "job_skills_str": state.get("job_skills_str", ""),
                                "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                "next_action": "official_search"  # Accept previous state even if above MAX_TARGET
                            }

                    # Check if we've gone below MIN_TARGET but still above 100
                    elif new_count < MIN_TARGET:
                        print(f"⚠️ Removal caused results to fall below minimum ({new_count} < {MIN_TARGET}).")
                        print("Stopping removal process to avoid going too low.")
                        return {
                            **state,
                            "current_conditions": updated_conditions,
                            "skills_used_count": updated_skills_count,
                            "relaxation_history": updated_history,
                            "optimization_path_history": updated_optimization_path,
                            "current_count": new_count,
                            "format_filter_conditions": format_filter_conditions,
                            "job_skills_str": None,  # job_main_skills, # TODO
                            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                            "next_action": "official_search"  # Accept the result even if below minimum
                        }

                    # If still too high but reduced, keep the reduction and continue
                    if new_count < current_count:
                        state = {
                            **state,
                            "current_conditions": updated_conditions,
                            "skills_used_count": updated_skills_count,
                            "relaxation_history": updated_history,
                            "optimization_path_history": updated_optimization_path,
                            "current_count": new_count,
                            "format_filter_conditions": format_filter_conditions,
                            "job_skills_str": None,  # job_main_skills, #TODO
                            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                        }
                        _node_in_tok = 0
                        _node_out_tok = 0
                        current_count = new_count
                        current_conditions = updated_conditions
                        skills_used_count = updated_skills_count
                        relaxation_history = updated_history
                        optimization_path_history = updated_optimization_path
        else:
            # No relaxation history - need to reduce from initial conditions
            print("No relaxation history. Attempting to reduce initial conditions...")

            priority_order = get_priority_order()

            # Continue reducing until within target or at minimum
            while current_count > MAX_TARGET:
                reduction_made = False

                # Try reducing each field in reverse priority order
                for field in reversed(priority_order):
                    if field == "skills":
                        # Try reducing the number of skills (keep minimum of 2)
                        if skills_used_count > 2:
                            print(f"Attempting to reduce skills from {skills_used_count} to {skills_used_count - 1}...")

                            updated_skills_count = skills_used_count - 1
                            pick_num = min(updated_skills_count, len(mandatory_skills))
                            job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=pick_num)

                            # (
                            #     search_results,
                            #     reranked_job_titles,
                            #     format_filter_conditions,
                            #     job_main_skills,
                            #     input_tokens,
                            #     output_tokens,
                            #     sales_nav_filters,
                            # ) = main_linkedin_search_process(
                            #     current_conditions, job_required_main_skills_list, llm
                            # )
                            # new_count = search_results["data"]["paging"]["total"]

                            search_results_info = get_linkedin_search_num(current_conditions,
                                                                          job_required_main_skills_list,
                                                                          llm)

                            new_count = search_results_info['search_results_num']
                            format_filter_conditions = search_results_info['format_filter_conditions']
                            _node_in_tok += search_results_info.get('input_tokens', 0)
                            _node_out_tok += search_results_info.get('output_tokens', 0)

                            print(f"After reducing skills: {new_count} results")

                            # Update optimization path history
                            updated_optimization_path = optimization_path_history.copy()
                            updated_optimization_path.append({
                                "action": "reduced_initial",
                                "field": "skills",
                                "value": f"from {skills_used_count} to {updated_skills_count}",
                                "count_change": new_count - current_count,
                                "new_count": new_count
                            })

                            # Check if we've gone too low after reduction (below 100)
                            if new_count < 100:
                                print(f"⚠️ Reduction caused results to drop too low ({new_count} < 100).")
                                print("Undoing last reduction and accepting previous state.")
                                # Revert to state before reduction - remove the last entry we just added
                                if updated_optimization_path and updated_optimization_path[-1].get(
                                        'action') == 'reduced_initial':
                                    updated_optimization_path.pop()
                                return {
                                    **state,
                                    "skills_used_count": skills_used_count,  # Keep original skills count
                                    "optimization_path_history": optimization_path_history,  # Keep original path
                                    "current_count": current_count,  # Keep original count (before reduction)
                                    "format_filter_conditions": state.get("format_filter_conditions", ""),
                                    "job_skills_str": state.get("job_skills_str", ""),
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"  # Accept previous state even if above MAX_TARGET
                                }

                            # Check if we've gone below MIN_TARGET but still above 100
                            elif new_count < MIN_TARGET:
                                print(
                                    f"⚠️ Reduction caused results to fall below minimum ({new_count} < {MIN_TARGET}).")
                                print("Stopping reduction process to avoid going too low.")
                                return {
                                    **state,
                                    "skills_used_count": updated_skills_count,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,  # job_main_skills, #TODO
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"  # Accept the result even if below minimum
                                }

                            if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                                print(f"✓ Successfully reduced to target range!")
                                return {
                                    **state,
                                    "skills_used_count": updated_skills_count,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,  # job_main_skills, #TODO
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"
                                }

                            # If reduced, keep the reduction
                            if new_count < current_count:
                                state = {
                                    **state,
                                    "skills_used_count": updated_skills_count,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,  # job_main_skills #TODO
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                }
                                _node_in_tok = 0
                                _node_out_tok = 0
                                current_count = new_count
                                skills_used_count = updated_skills_count
                                optimization_path_history = updated_optimization_path
                                reduction_made = True
                                break  # Start next iteration from the beginning
                        else:
                            print(f"Skills at minimum ({skills_used_count}), checking other conditions...")

                    elif field in current_conditions:
                        # Special handling for year_of_experience
                        if field == "year_of_experience":
                            # For year_of_experience, we need to remove it entirely
                            print(f"Attempting to remove year_of_experience constraint...")

                            updated_conditions = current_conditions.copy()
                            # Remove year_of_experience entirely
                            del updated_conditions["year_of_experience"]

                            # Test with reduced conditions
                            pick_num = min(skills_used_count, len(mandatory_skills))
                            job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=pick_num)

                            search_results_info = get_linkedin_search_num(updated_conditions,
                                                                          job_required_main_skills_list,
                                                                          llm)

                            new_count = search_results_info['search_results_num']
                            format_filter_conditions = search_results_info['format_filter_conditions']
                            _node_in_tok += search_results_info.get('input_tokens', 0)
                            _node_out_tok += search_results_info.get('output_tokens', 0)

                            print(f"After removing year_of_experience: {new_count} results")

                            # Get the value that was removed
                            removed_value = current_conditions["year_of_experience"]

                            # Update optimization path history
                            updated_optimization_path = optimization_path_history.copy()
                            updated_optimization_path.append({
                                "action": "reduced_initial",
                                "field": "year_of_experience",
                                "value": removed_value,
                                "count_change": new_count - current_count,
                                "new_count": new_count
                            })

                            # Check for drastic reduction
                            if current_count > MAX_TARGET and new_count < 100:
                                print(f"🔴 DRASTIC REDUCTION DETECTED: {current_count} → {new_count}")
                                print(f"  Field: year_of_experience, removed value: {removed_value}")
                                print("Saving state before reduction and attempting recovery...")

                                # Save the state BEFORE this drastic reduction
                                auto_state["drastic_reduction_detected"] = True
                                auto_state["state_before_drastic_reduction"] = {
                                    "conditions": current_conditions.copy(),
                                    "skills_count": skills_used_count,
                                    "relaxation_history": relaxation_history.copy(),
                                    "optimization_path": optimization_path_history.copy(),
                                    "count": current_count,
                                    "path_length": len(optimization_path_history),
                                    "format_filter_conditions": state.get("format_filter_conditions", ""),
                                    "job_skills_str": state.get("job_skills_str", "")
                                }
                                auto_state["recovery_attempts"] = 0

                                # Continue with the reduction but switch to recovery mode
                                return {
                                    **state,
                                    "current_conditions": updated_conditions,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,
                                    "auto_relaxation_state": auto_state,
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "auto_relaxation"
                                }
                            else:
                                # Normal case: just check thresholds
                                if new_count < 100:
                                    print(f"⚠️ Reduction caused results to drop too low ({new_count} < 100).")
                                    print("Undoing last reduction and accepting previous state.")

                                    # Revert to state before reduction
                                    if updated_optimization_path and updated_optimization_path[-1].get(
                                            'action') == 'reduced_initial':
                                        updated_optimization_path.pop()

                                    return {
                                        **state,
                                        "current_conditions": current_conditions,  # Keep ORIGINAL conditions
                                        "optimization_path_history": optimization_path_history,  # Keep original path
                                        "current_count": current_count,  # Keep original count
                                        "format_filter_conditions": state.get("format_filter_conditions", ""),
                                        "job_skills_str": state.get("job_skills_str", ""),
                                        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                        "next_action": "official_search"
                                    }

                            # Check MIN_TARGET threshold
                            if new_count < MIN_TARGET:
                                print(
                                    f"⚠️ Reduction caused results to fall below MIN_TARGET ({new_count} < {MIN_TARGET}).")
                                print("Undoing last reduction and accepting previous state.")

                                # Revert to state before reduction
                                if updated_optimization_path and updated_optimization_path[-1].get(
                                        'action') == 'reduced_initial':
                                    updated_optimization_path.pop()

                                return {
                                    **state,
                                    "current_conditions": current_conditions,  # Keep ORIGINAL conditions
                                    "optimization_path_history": optimization_path_history,  # Keep original path
                                    "current_count": current_count,  # Keep original count
                                    "format_filter_conditions": state.get("format_filter_conditions", ""),
                                    "job_skills_str": state.get("job_skills_str", ""),
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"
                                }

                            if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                                print(f"✓ Successfully reduced to target range!")
                                return {
                                    **state,
                                    "current_conditions": updated_conditions,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"
                                }

                            # If reduced, keep the reduction
                            if new_count < current_count:
                                state = {
                                    **state,
                                    "current_conditions": updated_conditions,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                }
                                _node_in_tok = 0
                                _node_out_tok = 0
                                current_count = new_count
                                current_conditions = updated_conditions
                                optimization_path_history = updated_optimization_path
                                reduction_made = True
                                break  # Start next iteration from the beginning

                        # Try removing values from this field (for non-year_of_experience fields)
                        if field.lower().strip() == "location":
                            field_values = current_conditions[field].get('name', [])
                        else:
                            field_values = current_conditions.get(field, [])

                        if not isinstance(field_values, list):
                            field_values = [field_values]

                        # Only try to remove if there are multiple values
                        if len(field_values) > 1:
                            print(
                                f"Attempting to reduce {field} from {len(field_values)} to {len(field_values) - 1} values...")

                            # Remove the last value
                            updated_conditions = current_conditions.copy()
                            new_values = field_values[:-1]  # Remove last value

                            # Double-check that we're not creating an empty list
                            if len(new_values) == 0:
                                print(f"  Warning: Cannot remove last value from {field} - would leave field empty")
                                continue

                            if field.lower().strip() == "location":
                                updated_conditions[field] = {'name': new_values}
                            else:
                                updated_conditions[field] = new_values

                            # Test with reduced conditions
                            pick_num = min(skills_used_count, len(mandatory_skills))
                            job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=pick_num)

                            # (
                            #     search_results,
                            #     reranked_job_titles,
                            #     format_filter_conditions,
                            #     job_main_skills,
                            #     input_tokens,
                            #     output_tokens,
                            #     sales_nav_filters,
                            # ) = main_linkedin_search_process(
                            #     updated_conditions, job_required_main_skills_list, llm
                            # )
                            # new_count = search_results["data"]["paging"]["total"]

                            search_results_info = get_linkedin_search_num(updated_conditions,
                                                                          job_required_main_skills_list,
                                                                          llm)

                            new_count = search_results_info['search_results_num']
                            format_filter_conditions = search_results_info['format_filter_conditions']
                            _node_in_tok += search_results_info.get('input_tokens', 0)
                            _node_out_tok += search_results_info.get('output_tokens', 0)

                            print(f"After reducing {field}: {new_count} results")

                            # Update optimization path history
                            updated_optimization_path = optimization_path_history.copy()
                            removed_value = field_values[-1]
                            updated_optimization_path.append({
                                "action": "reduced_initial",
                                "field": field,
                                "value": removed_value,
                                "count_change": new_count - current_count,
                                "new_count": new_count
                            })

                            # Check if we've gone too low after reduction (below 100 is critical)
                            if new_count < 100:
                                # Check if this is a drastic reduction (from > MAX_TARGET to < 100)
                                if current_count > MAX_TARGET:
                                    print(f"🔴 DRASTIC REDUCTION DETECTED: {current_count} → {new_count}")
                                    print(f"  Field: {field}, removed value: {removed_value}")
                                    print("Saving state before reduction and attempting recovery...")

                                    # Save the state before this drastic reduction
                                    auto_state["drastic_reduction_detected"] = True
                                    auto_state["state_before_drastic_reduction"] = {
                                        "conditions": current_conditions.copy(),
                                        "skills_count": skills_used_count,
                                        "relaxation_history": relaxation_history.copy(),
                                        "optimization_path": optimization_path_history.copy(),
                                        "count": current_count,
                                        "path_length": len(optimization_path_history),
                                        "format_filter_conditions": state.get("format_filter_conditions", ""),
                                        "job_skills_str": state.get("job_skills_str", "")
                                    }
                                    auto_state["recovery_attempts"] = 0  # Initialize recovery counter

                                    # Continue with the reduction but switch to recovery mode
                                    # Apply the reduction and immediately return to start recovery
                                    return {
                                        **state,
                                        "current_conditions": updated_conditions,
                                        "optimization_path_history": updated_optimization_path,
                                        "current_count": new_count,
                                        "format_filter_conditions": format_filter_conditions,
                                        "job_skills_str": None,  # job_main_skills, #TODO: fill in job_skills_str
                                        "auto_relaxation_state": auto_state,
                                        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                        "next_action": "auto_relaxation"  # Continue to recovery mode immediately
                                    }
                                else:
                                    # Normal case: just undo the reduction
                                    print(f"⚠️ Reduction caused results to drop too low ({new_count} < 100).")
                                    print("Undoing last reduction and accepting previous state.")

                                    # Revert to state before reduction - remove the last entry we just added
                                    if updated_optimization_path and updated_optimization_path[-1].get(
                                            'action') == 'reduced_initial':
                                        updated_optimization_path.pop()

                                    # Return state with ORIGINAL conditions (before reduction)
                                    return {
                                        **state,
                                        "current_conditions": current_conditions,  # Keep ORIGINAL conditions
                                        "optimization_path_history": optimization_path_history,  # Keep original path
                                        "current_count": current_count,  # Keep original count (before reduction)
                                        "format_filter_conditions": state.get("format_filter_conditions", ""),
                                        "job_skills_str": state.get("job_skills_str", ""),
                                        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                        "next_action": "official_search"
                                        # Accept previous state even if above MAX_TARGET
                                    }

                            # Also check MIN_TARGET as a secondary threshold
                            elif new_count < MIN_TARGET:
                                print(
                                    f"⚠️ Reduction caused results to fall below MIN_TARGET ({new_count} < {MIN_TARGET}).")
                                print("Undoing last reduction and accepting previous state.")

                                # Revert to state before reduction - remove the last entry we just added
                                if updated_optimization_path and updated_optimization_path[-1].get(
                                        'action') == 'reduced_initial':
                                    updated_optimization_path.pop()

                                # Return state with ORIGINAL conditions (before reduction)
                                return {
                                    **state,
                                    "current_conditions": current_conditions,  # Keep ORIGINAL conditions
                                    "optimization_path_history": optimization_path_history,  # Keep original path
                                    "current_count": current_count,  # Keep original count (before reduction)
                                    "format_filter_conditions": state.get("format_filter_conditions", ""),
                                    "job_skills_str": state.get("job_skills_str", ""),
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"  # Accept previous state even if above MAX_TARGET
                                }

                            if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                                print(f"✓ Successfully reduced to target range!")
                                return {
                                    **state,
                                    "current_conditions": updated_conditions,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,  # job_main_skills, # TODO
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"
                                }

                            # If reduced, keep the reduction
                            if new_count < current_count:
                                state = {
                                    **state,
                                    "current_conditions": updated_conditions,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,  # job_main_skills # TODO
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                }
                                _node_in_tok = 0
                                _node_out_tok = 0
                                current_count = new_count
                                current_conditions = updated_conditions
                                optimization_path_history = updated_optimization_path
                                reduction_made = True
                                break  # Start next iteration from the beginning

                # Check if we can make any more reductions
                if not reduction_made:
                    # Check if all conditions are at minimum
                    at_minimum = True

                    # Check skills minimum
                    if skills_used_count > 2:
                        at_minimum = False

                    # Check other fields minimum
                    for field in current_conditions:
                        if field.lower().strip() == "location":
                            field_values = current_conditions[field].get('name', [])
                        else:
                            field_values = current_conditions.get(field, [])

                        if not isinstance(field_values, list):
                            field_values = [field_values]

                        if len(field_values) > 1:
                            at_minimum = False
                            break

                    if at_minimum:
                        print("\n⚠️  All conditions are at minimum values:")
                        print(f"  - Skills: {skills_used_count} (minimum is 2)")
                        for field in current_conditions:
                            if field.lower().strip() == "location":
                                field_values = current_conditions[field].get('name', [])
                            else:
                                field_values = current_conditions.get(field, [])
                            if not isinstance(field_values, list):
                                field_values = [field_values]
                            print(f"  - {field}: {len(field_values)} value(s)")
                        print(f"Cannot reduce further. Current results: {current_count}")
                        break

        # If we couldn't reduce to target, accept the result
        print("Cannot reduce further. Accepting current results.")
        return {
            **state,
            "optimization_path_history": optimization_path_history,  # Ensure history is included
            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
            "next_action": "official_search"
        }

    # If we're below minimum, need to add conditions
    if current_count < MIN_TARGET:
        # Check if we're in drastic reduction recovery mode
        if auto_state.get("drastic_reduction_detected", False) and current_count < 100:
            print("🔴 RECOVERY MODE: Attempting to recover from drastic reduction...")
            print(f"  Current count: {current_count} (still below 100)")

            # Track recovery attempts
            recovery_attempts = auto_state.get("recovery_attempts", 0)
            print(f"  Recovery attempts so far: {recovery_attempts}")

            # If we've tried multiple times and still below 100, fail recovery
            if recovery_attempts >= 3:
                print(f"🔴 RECOVERY FAILED: After {recovery_attempts} attempts, still below 100")
                print("Setting recovery_failed flag for rollback...")
                auto_state["recovery_failed"] = True

                # Return to trigger rollback on next iteration
                return {
                    **state,
                    "auto_relaxation_state": auto_state,
                    "optimization_path_history": optimization_path_history,
                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                    "next_action": "auto_relaxation"  # Continue to next iteration for rollback
                }

            # Increment recovery attempt counter BEFORE trying to add conditions
            # This ensures we count attempts even if no conditions can be added
            auto_state["recovery_attempts"] = recovery_attempts + 1
            print(f"  Starting recovery attempt #{auto_state['recovery_attempts']}...")
        else:
            print(f"Results ({current_count}) below minimum. Adding conditions by priority...")

        print(
            f"DEBUG [node_auto_relaxation]: Starting with optimization_path_history of {len(optimization_path_history)} entries")

        priority_order = get_priority_order()
        conditions_added = False
        highest_count_achieved = current_count  # Track the highest count we can achieve

        for field in priority_order:
            print(f"DEBUG [node_auto_relaxation]: Checking field '{field}'...")
            # DEBUG: Print current_conditions for seniority to diagnose the issue
            if field == "seniority":
                print(f"DEBUG [node_auto_relaxation]: current_conditions['seniority'] = {current_conditions.get('seniority', 'NOT FOUND')}")
                print(f"DEBUG [node_auto_relaxation]: relaxation_options['seniority'] = {relaxation_options.get('seniority', 'NOT FOUND')}")
            # Check if we can add more conditions for this field
            if can_add_more_conditions(field, current_conditions, relaxation_options,
                                       mandatory_skills, skills_used_count):
                print(f"DEBUG [node_auto_relaxation]: Can add more conditions for '{field}'")

                # Get the next condition to add
                next_option = get_next_condition_to_add(
                    field, current_conditions, relaxation_options,
                    mandatory_skills, skills_used_count
                )

                if next_option:
                    print(f"\nAdding to {field}: {next_option}")

                    # Apply the condition (pass relaxation_options for seniority year mapping)
                    updated_conditions, updated_skills_count = apply_condition(
                        field, next_option, current_conditions, skills_used_count, relaxation_options
                    )

                    # Test with the new condition
                    pick_num = min(updated_skills_count, len(mandatory_skills))
                    job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=pick_num)

                    print("updated_conditions:", updated_conditions)

                    # (
                    #     search_results,
                    #     reranked_job_titles,
                    #     format_filter_conditions,
                    #     job_main_skills,
                    #     input_tokens,
                    #     output_tokens,
                    #     sales_nav_filters,
                    # ) = main_linkedin_search_process(
                    #     updated_conditions, job_required_main_skills_list, llm
                    # )
                    # new_count = search_results['count']  # nav api: #search_results["data"]["paging"]["total"]

                    search_results_info = get_linkedin_search_num(updated_conditions,
                                                                  job_required_main_skills_list,
                                                                  llm)

                    new_count = search_results_info['search_results_num']
                    format_filter_conditions = search_results_info['format_filter_conditions']
                    _node_in_tok += search_results_info.get('input_tokens', 0)
                    _node_out_tok += search_results_info.get('output_tokens', 0)

                    print(f"After adding: {new_count} results (increase: +{new_count - current_count})")

                    # Update both histories
                    updated_history = relaxation_history.copy()
                    updated_history.append({
                        "field": field,
                        "added": next_option,
                        "count_increase": new_count - current_count
                    })

                    # Update optimization path history with the addition
                    updated_optimization_path = optimization_path_history.copy()
                    updated_optimization_path.append({
                        "action": "added",
                        "field": field,
                        "value": next_option,
                        "count_change": new_count - current_count,
                        "new_count": new_count
                    })

                    # Special handling for drastic reduction recovery
                    if auto_state.get("drastic_reduction_detected", False):
                        # Note: recovery_attempts was already incremented before trying to add conditions
                        print(f"  Recovery result for attempt #{auto_state['recovery_attempts']}: {new_count} results")

                        # Check if we've exhausted recovery attempts while still below 100
                        if new_count < 100 and auto_state.get("recovery_attempts", 0) >= 3:
                            print(
                                f"🔴 RECOVERY FAILED: After {auto_state['recovery_attempts']} attempts, still below 100")
                            print("Triggering rollback to pre-reduction state...")

                            # Get the saved state before drastic reduction
                            saved_state = auto_state.get("state_before_drastic_reduction", {})
                            if saved_state:
                                # Add rollback entry to optimization path
                                rollback_optimization_path = updated_optimization_path.copy()
                                rollback_optimization_path.append({
                                    "action": "rollback",
                                    "field": "system",
                                    "value": f"Rolled back after {auto_state['recovery_attempts']} failed recovery attempts",
                                    "count_change": saved_state.get("count", new_count) - new_count,
                                    "new_count": saved_state.get("count", new_count)
                                })

                                # Clear the drastic reduction flags
                                auto_state_cleared = {}

                                return {
                                    **state,
                                    "current_conditions": saved_state.get("conditions", current_conditions),
                                    "skills_used_count": saved_state.get("skills_count", skills_used_count),
                                    "relaxation_history": saved_state.get("relaxation_history", relaxation_history),
                                    "optimization_path_history": rollback_optimization_path,
                                    "current_count": saved_state.get("count", current_count),
                                    "format_filter_conditions": saved_state.get("format_filter_conditions",
                                                                                state.get("format_filter_conditions",
                                                                                          "")),
                                    "job_skills_str": saved_state.get("job_skills_str",
                                                                      state.get("job_skills_str", "")),
                                    "auto_relaxation_state": auto_state_cleared,
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search"  # Accept the pre-reduction state as final
                                }

                        # If we're recovering and the new count is above 100
                        if new_count >= 100:
                            if new_count > MAX_TARGET:
                                # We've recovered but exceeded MAX_TARGET
                                # This means the only way to get above 100 is to exceed MAX_TARGET
                                # Accept the pre-reduction state as final
                                print(f"🔄 RECOVERY: Count recovered to {new_count} (exceeds MAX_TARGET)")
                                print("The only way to get above 100 is to exceed MAX_TARGET.")
                                print("Accepting pre-reduction state as final...")

                                saved_state = auto_state.get("state_before_drastic_reduction", {})
                                if saved_state:
                                    # Add rollback entry to optimization path to show what happened
                                    rollback_optimization_path = optimization_path_history.copy()
                                    rollback_optimization_path.append({
                                        "action": "rollback",
                                        "field": "system",
                                        "value": f"Rolled back to state before drastic reduction",
                                        "count_change": saved_state.get("count", current_count) - current_count,
                                        "new_count": saved_state.get("count", current_count)
                                    })

                                    # Clear the drastic reduction flags
                                    auto_state_cleared = auto_state.copy()
                                    auto_state_cleared.pop("drastic_reduction_detected", None)
                                    auto_state_cleared.pop("state_before_drastic_reduction", None)
                                    auto_state_cleared.pop("recovery_failed", None)

                                    return {
                                        **state,
                                        "current_conditions": saved_state.get("conditions", current_conditions),
                                        "skills_used_count": saved_state.get("skills_count", skills_used_count),
                                        "relaxation_history": saved_state.get("relaxation_history", relaxation_history),
                                        "optimization_path_history": rollback_optimization_path,
                                        # Use the path with rollback entry
                                        "current_count": saved_state.get("count", current_count),
                                        "format_filter_conditions": saved_state.get("format_filter_conditions",
                                                                                    state.get(
                                                                                        "format_filter_conditions",
                                                                                        "")),
                                        "job_skills_str": saved_state.get("job_skills_str",
                                                                          state.get("job_skills_str", "")),
                                        "auto_relaxation_state": auto_state_cleared,
                                        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                        "next_action": "official_search"  # Accept as final
                                    }
                            else:
                                # Successfully recovered within range
                                print(f"✅ RECOVERY SUCCESSFUL: Count recovered to {new_count}")
                                # Clear recovery flags
                                auto_state_cleared = auto_state.copy()
                                auto_state_cleared.pop("drastic_reduction_detected", None)
                                auto_state_cleared.pop("state_before_drastic_reduction", None)
                                auto_state_cleared.pop("recovery_failed", None)

                                return {
                                    **state,
                                    "current_conditions": updated_conditions,
                                    "skills_used_count": updated_skills_count,
                                    "relaxation_history": updated_history,
                                    "optimization_path_history": updated_optimization_path,
                                    "current_count": new_count,
                                    "format_filter_conditions": format_filter_conditions,
                                    "job_skills_str": None,  # job_main_skills, # TODO
                                    "auto_relaxation_state": auto_state_cleared,
                                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                                    "next_action": "official_search" if is_within_target_range(new_count, MIN_TARGET,
                                                                                               MAX_TARGET) else "auto_relaxation"
                                }

                    # Check if we're now in target range (normal case)
                    if is_within_target_range(new_count, MIN_TARGET, MAX_TARGET):
                        print(f"✓ Successfully reached target range!")
                        return {
                            **state,
                            "current_conditions": updated_conditions,
                            "skills_used_count": updated_skills_count,
                            "relaxation_history": updated_history,
                            "optimization_path_history": updated_optimization_path,
                            "current_count": new_count,
                            "format_filter_conditions": format_filter_conditions,
                            "job_skills_str": None,  # job_main_skills, # TODO
                            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                            "next_action": "official_search"
                        }

                    # Update state and continue
                    state = {
                        **state,
                        "current_conditions": updated_conditions,
                        "skills_used_count": updated_skills_count,
                        "relaxation_history": updated_history,
                        "optimization_path_history": updated_optimization_path,
                        "current_count": new_count,
                        "format_filter_conditions": format_filter_conditions,
                        "job_skills_str": None,  # job_main_skills, # TODO
                        "auto_relaxation_state": auto_state,  # CRITICAL: Preserve recovery attempt counter
                        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                    }
                    _node_in_tok = 0
                    _node_out_tok = 0
                    current_count = new_count
                    current_conditions = updated_conditions
                    skills_used_count = updated_skills_count
                    relaxation_history = updated_history
                    optimization_path_history = updated_optimization_path
                    conditions_added = True

                    # Continue to next iteration automatically
                    # Make sure to include ALL state updates when returning for next iteration
                    print(
                        f"DEBUG [node_auto_relaxation]: About to return for next iteration with {len(updated_optimization_path)} entries in optimization_path_history")
                    return {
                        **state,
                        "current_conditions": updated_conditions,
                        "skills_used_count": updated_skills_count,
                        "relaxation_history": updated_history,
                        "optimization_path_history": updated_optimization_path,
                        "current_count": new_count,
                        "format_filter_conditions": format_filter_conditions,
                        "job_skills_str": None,  # job_main_skills, # TODO
                        "auto_relaxation_state": auto_state,  # CRITICAL: Preserve recovery attempt counter
                        "next_action": "auto_relaxation"
                    }

        # If no more conditions can be added
        if not conditions_added:
            print("\n⚠️  All available conditions have been added.")
            print(f"Final result count: {current_count}")

            # Check if we're in drastic reduction recovery mode and still below 100
            if auto_state.get("drastic_reduction_detected", False) and current_count < 100:
                print(f"🔴 RECOVERY FAILED: Cannot get results above 100 (current: {current_count})")
                print("Performing rollback to pre-reduction state...")

                # Get the saved state before drastic reduction
                saved_state = auto_state.get("state_before_drastic_reduction", {})
                if saved_state:
                    # Add rollback entry to optimization path
                    rollback_optimization_path = optimization_path_history.copy()
                    rollback_optimization_path.append({
                        "action": "rollback",
                        "field": "system",
                        "value": f"Rolled back after exhausting all recovery options",
                        "count_change": saved_state.get("count", current_count) - current_count,
                        "new_count": saved_state.get("count", current_count)
                    })

                    # Clear the drastic reduction flags
                    auto_state_cleared = {}

                    return {
                        **state,
                        "current_conditions": saved_state.get("conditions", current_conditions),
                        "skills_used_count": saved_state.get("skills_count", skills_used_count),
                        "relaxation_history": saved_state.get("relaxation_history", relaxation_history),
                        "optimization_path_history": rollback_optimization_path,
                        "current_count": saved_state.get("count", current_count),
                        "format_filter_conditions": saved_state.get("format_filter_conditions",
                                                                    state.get("format_filter_conditions", "")),
                        "job_skills_str": saved_state.get("job_skills_str", state.get("job_skills_str", "")),
                        "auto_relaxation_state": auto_state_cleared,
                        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                        "next_action": "official_search"  # Accept the pre-reduction state as final
                    }

            if current_count < MIN_TARGET:
                print(f"Warning: Could not reach minimum target of {MIN_TARGET}")

            # Fallback: If results are still under 50, try removing Industry filter
            if current_count < 50 and current_conditions.get("industry") and state.get("allow_remove_industry", True):
                print(f"\n🔄 FALLBACK: Results ({current_count}) still under 50 after all optimizations.")
                print("Attempting to remove 'Industry' filter...")

                # Save original industry for logging
                original_industry = current_conditions.get("industry", [])

                # Remove industry filter
                fallback_conditions = current_conditions.copy()
                fallback_conditions.pop("industry", None)

                # Test search without industry
                pick_num = min(skills_used_count, len(mandatory_skills))
                job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=pick_num)

                fallback_search_info = get_linkedin_search_num(
                    fallback_conditions,
                    job_required_main_skills_list,
                    llm
                )

                fallback_count = fallback_search_info['search_results_num']
                _node_in_tok += fallback_search_info.get('input_tokens', 0)
                _node_out_tok += fallback_search_info.get('output_tokens', 0)
                print(f"Results after removing Industry filter: {fallback_count} (was {current_count})")

                # Always remove industry when results are under 50, regardless of count change
                print(f"✅ Removing Industry filter (results under 50): {current_count} -> {fallback_count}")

                # Update optimization path history
                updated_optimization_path = optimization_path_history.copy()
                updated_optimization_path.append({
                    "action": "removed",
                    "field": "industry",
                    "value": f"Removed industry filter: {original_industry}",
                    "count_change": fallback_count - current_count,
                    "new_count": fallback_count
                })

                return {
                    **state,
                    "current_conditions": fallback_conditions,
                    "current_count": fallback_count,
                    "format_filter_conditions": fallback_search_info.get('format_filter_conditions', state.get('format_filter_conditions', '')),
                    "optimization_path_history": updated_optimization_path,
                    "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                    "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                    "next_action": "official_search"
                }

            return {
                **state,
                "optimization_path_history": optimization_path_history,  # Ensure history is included
                "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
                "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
                "next_action": "official_search"
            }

    # Final fallback: If results are still under 50, try removing Industry filter
    if current_count < 50 and current_conditions.get("industry") and state.get("allow_remove_industry", True):
        print(f"\n🔄 FINAL FALLBACK: Results ({current_count}) still under 50.")
        print("Attempting to remove 'Industry' filter as last resort...")

        # Save original industry for logging
        original_industry = current_conditions.get("industry", [])

        # Remove industry filter
        fallback_conditions = current_conditions.copy()
        fallback_conditions.pop("industry", None)

        # Test search without industry
        pick_num = min(skills_used_count, len(mandatory_skills))
        job_required_main_skills_list = choose_job_main_skills(mandatory_skills, pick_num=pick_num)

        fallback_search_info = get_linkedin_search_num(
            fallback_conditions,
            job_required_main_skills_list,
            llm
        )

        fallback_count = fallback_search_info['search_results_num']
        _node_in_tok += fallback_search_info.get('input_tokens', 0)
        _node_out_tok += fallback_search_info.get('output_tokens', 0)
        print(f"Results after removing Industry filter: {fallback_count} (was {current_count})")

        # Always remove industry when results are under 50, regardless of count change
        print(f"✅ Removing Industry filter (results under 50): {current_count} -> {fallback_count}")

        # Update optimization path history
        updated_optimization_path = optimization_path_history.copy()
        updated_optimization_path.append({
            "action": "removed",
            "field": "industry",
            "value": f"Removed industry filter: {original_industry}",
            "count_change": fallback_count - current_count,
            "new_count": fallback_count
        })

        return {
            **state,
            "current_conditions": fallback_conditions,
            "current_count": fallback_count,
            "format_filter_conditions": fallback_search_info.get('format_filter_conditions', state.get('format_filter_conditions', '')),
            "optimization_path_history": updated_optimization_path,
            "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
            "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
            "next_action": "official_search"
        }

    return {
        **state,
        "optimization_path_history": optimization_path_history,  # Ensure history is included
        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
        "next_action": "official_search"
    }


def node_official_search(state: SearchState) -> SearchState:
    """Node 5: Execute official search"""
    print("\n=== Node 5: Official Search ===")

    _node_in_tok = 0
    _node_out_tok = 0

    print(f"✓ Final conditions: {state['current_conditions']}")
    print(f"✓ Total results: {state['current_count']}")
    print(f"✓ Relaxations applied: {len(state.get('relaxation_history', []))}")

    print(
        f"DEBUG: In node_official_search, optimization_path_history has {len(state.get('optimization_path_history', []))} entries")
    if state.get('optimization_path_history'):
        print(f"DEBUG: First entry in node_official_search: {state['optimization_path_history'][0]}")

    if state.get('relaxation_history'):
        print("\n📋 Relaxation History (Added Conditions Only):")
        for idx, relax in enumerate(state['relaxation_history'], 1):
            print(f"  {idx}. [{relax['field']}] Added {relax['added']} (+{relax['count_increase']} results)")

    if state.get('optimization_path_history'):
        print("\n🔄 Full Optimization Path (All Changes):")
        for idx, step in enumerate(state['optimization_path_history'], 1):
            if step.get('action') == 'initial':
                print(f"  {idx}. [{step['field']}] 🎯 Initial search: {step['value']} → {step['new_count']} results")
            elif step.get('action') == 'added':
                print(
                    f"  {idx}. [{step['field']}] ➕ Added {step['value']} → {step['new_count']} results (change: {step['count_change']:+d})")
            elif step.get('action') == 'removed':
                print(
                    f"  {idx}. [{step['field']}] ➖ Removed {step['value']} → {step['new_count']} results (change: {step['count_change']:+d})")
            elif step.get('action') == 'reduced_initial':
                print(
                    f"  {idx}. [{step['field']}] ⬇️  Reduced initial {step['value']} → {step['new_count']} results (change: {step['count_change']:+d})")
            elif step.get('action') == 'tightened':
                print(
                    f"  {idx}. [{step['field']}] 🔧 Tightened: Added {step['value']} → {step['new_count']} results (change: {step['count_change']:+d})")
            elif step.get('action') == 'rollback':
                print(
                    f"  {idx}. [{step['field']}] 🔄 ROLLBACK: {step['value']} → {step['new_count']} results (restored {step['count_change']:+d} results)")

    print(f"\n✓ Filter: {state['format_filter_conditions']}...")
    print(f"✓ Keywords: {state['job_skills_str']}")
    print(f"✓ Final skills count: {state.get('skills_used_count', 0)} skills")
    print("\n✓ Ready for official search execution")

    model_type = "gpt-4.1"
    temperature = 0.3
    max_tokens = 2048
    llm = ChatGPTWrapper()

    mandatory_skills_list = state["mandatory_skills"]
    skills_used_count = state.get("skills_used_count", 2)

    job_required_main_skills_list = choose_job_main_skills(mandatory_skills_list, pick_num=skills_used_count)
    current_conditions = state["current_conditions"]

    search_results_info = get_linkedin_search_num(current_conditions,
                                                  job_required_main_skills_list,
                                                  llm)
    search_results_num = search_results_info['search_results_num']
    format_filter_conditions = search_results_info['format_filter_conditions']
    _node_in_tok += search_results_info.get('input_tokens', 0)
    _node_out_tok += search_results_info.get('output_tokens', 0)
    optimization_path_history = state.get("optimization_path_history", [])

    # Final fallback in official search: If results are still under 50, try removing industry filter first
    if search_results_num < 50 and current_conditions.get("industry") and state.get("allow_remove_industry", True):
        print(f"\n🔄 FINAL FALLBACK (Official Search): Results ({search_results_num}) still under 50.")
        print("Attempting to remove 'industry' filter...")

        original_industry = current_conditions.get("industry", [])
        fallback_conditions = current_conditions.copy()
        fallback_conditions.pop("industry", None)

        fallback_search_info = get_linkedin_search_num(
            fallback_conditions,
            job_required_main_skills_list,
            llm
        )
        fallback_count = fallback_search_info['search_results_num']
        _node_in_tok += fallback_search_info.get('input_tokens', 0)
        _node_out_tok += fallback_search_info.get('output_tokens', 0)
        print(f"Results after removing industry filter: {fallback_count} (was {search_results_num})")

        # Always remove industry when results are under 50, regardless of count change
        print(f"✅ Removing industry filter (results under 50): {search_results_num} -> {fallback_count}")
        optimization_path_history = optimization_path_history.copy()
        optimization_path_history.append({
            "action": "removed",
            "field": "industry",
            "value": f"Removed industry filter: {original_industry}",
            "count_change": fallback_count - search_results_num,
            "new_count": fallback_count
        })
        current_conditions = fallback_conditions
        search_results_num = fallback_count
        format_filter_conditions = fallback_search_info['format_filter_conditions']

    # Final fallback: If results are still under 10, remove job_function filter
    if search_results_num < 10 and current_conditions.get("job_function"):
        print(f"\n🔄 FINAL FALLBACK (Official Search): Results ({search_results_num}) still under 10.")
        print("Attempting to remove 'job_function' filter...")

        original_job_function = current_conditions.get("job_function", [])
        fallback_conditions = current_conditions.copy()
        fallback_conditions.pop("job_function", None)

        fallback_search_info = get_linkedin_search_num(
            fallback_conditions,
            job_required_main_skills_list,
            llm
        )
        fallback_count = fallback_search_info['search_results_num']
        _node_in_tok += fallback_search_info.get('input_tokens', 0)
        _node_out_tok += fallback_search_info.get('output_tokens', 0)
        print(f"Results after removing job_function filter: {fallback_count} (was {search_results_num})")

        # Always remove job_function when results are under 10, regardless of count change
        print(f"✅ Removing job_function filter (results under 10): {search_results_num} -> {fallback_count}")
        optimization_path_history = optimization_path_history.copy()
        optimization_path_history.append({
            "action": "removed",
            "field": "job_function",
            "value": f"Removed job_function filter: {original_job_function}",
            "count_change": fallback_count - search_results_num,
            "new_count": fallback_count
        })
        current_conditions = fallback_conditions
        search_results_num = fallback_count
        format_filter_conditions = fallback_search_info['format_filter_conditions']

    # Final fallback in official search: If results are still under 50, expand year_of_experience range
    # Always apply expansion when results are critically low (under 50)
    if search_results_num < 50 and current_conditions.get("year_of_experience"):
        print(f"\n🔄 FINAL FALLBACK (Official Search): Results ({search_results_num}) still under 50.")
        print("Expanding 'year_of_experience' range...")

        original_yoe = current_conditions.get("year_of_experience", {})
        original_start = original_yoe.get("start_num_year", 0)
        original_end = original_yoe.get("end_num_year", 0)

        # Expand range: reduce start by 1 (min 0) and increase end by max(end*0.5, 2), capped at 30
        expanded_start = max(0, original_start - 1)
        expanded_end = min(original_end + int(max(original_end * 0.5, 2)), 30)

        print(f"Expanding year_of_experience from [{original_start}-{original_end}] to [{expanded_start}-{expanded_end}]")

        fallback_conditions = current_conditions.copy()
        fallback_conditions["year_of_experience"] = {
            "start_num_year": expanded_start,
            "end_num_year": expanded_end
        }

        fallback_search_info = get_linkedin_search_num(
            fallback_conditions,
            job_required_main_skills_list,
            llm
        )
        fallback_count = fallback_search_info['search_results_num']
        _node_in_tok += fallback_search_info.get('input_tokens', 0)
        _node_out_tok += fallback_search_info.get('output_tokens', 0)
        print(f"Results after expanding year_of_experience: {fallback_count} (was {search_results_num})")

        # Always apply expansion when results are under 50 (critical situation)
        print(f"✅ year_of_experience expanded from [{original_start}-{original_end}] to [{expanded_start}-{expanded_end}]")
        optimization_path_history = optimization_path_history.copy()
        optimization_path_history.append({
            "action": "expanded",
            "field": "year_of_experience",
            "value": f"Expanded from [{original_start}-{original_end}] to [{expanded_start}-{expanded_end}]",
            "count_change": fallback_count - search_results_num,
            "new_count": fallback_count
        })
        current_conditions = fallback_conditions
        search_results_num = fallback_count
        format_filter_conditions = fallback_search_info['format_filter_conditions']

    output_state = {
        **state,
        "current_conditions": current_conditions,
        "current_count": search_results_num,  # Update current_count to match the final search result
        "format_filter_conditions": format_filter_conditions,
        "search_results_num": search_results_num,
        "optimization_path_history": optimization_path_history,
        "job_required_main_skills_list": job_required_main_skills_list,
        "sales_nav_filters": format_filter_conditions,
        "total_input_tokens": state.get("total_input_tokens", 0) + _node_in_tok,
        "total_output_tokens": state.get("total_output_tokens", 0) + _node_out_tok,
        "next_action": "end",
    }

    return output_state


def route_next_action(state: SearchState) -> Literal[
    "ask_user", "analyze_relaxation", "check_further", "official_search", "auto_relaxation"]:
    """Router function"""
    action_map = {
        "ask_user": "ask_user",
        "analyze_relaxation": "analyze_relaxation",
        "check_further_relaxation": "check_further",
        "official_search": "official_search",
        "auto_relaxation": "auto_relaxation"
    }
    return action_map.get(state.get("next_action", "ask_user"), "official_search")


def build_graph():
    """Build LangGraph workflow"""
    workflow = StateGraph(SearchState)

    workflow.add_node("initial_search", node_initial_search)
    workflow.add_node("ask_user", node_ask_user_satisfaction)
    workflow.add_node("analyze_relaxation", node_analyze_relaxation)
    workflow.add_node("check_further", node_check_further_relaxation)
    workflow.add_node("auto_relaxation", node_auto_relaxation)
    workflow.add_node("official_search", node_official_search)

    workflow.set_entry_point("initial_search")

    workflow.add_conditional_edges("initial_search", route_next_action)
    workflow.add_conditional_edges("ask_user", route_next_action)
    workflow.add_conditional_edges("analyze_relaxation", route_next_action)
    workflow.add_conditional_edges("check_further", route_next_action)
    workflow.add_conditional_edges("auto_relaxation", route_next_action)
    workflow.add_edge("official_search", END)

    return workflow.compile(checkpointer=MemorySaver())


def format_optimization_path(optimization_path_history):
    """
    Format the optimization path history for readable display.
    Similar to the formatting in streamlit_search_optimization.py
    """
    if not optimization_path_history:
        return "No optimization path recorded."

    formatted_output = []
    formatted_output.append("\n🔄 Full Optimization Path:")
    formatted_output.append("-" * 60)

    for idx, step in enumerate(optimization_path_history, 1):
        action = step.get('action', '')
        field = step.get('field', '')
        value = step.get('value', '') if step.get('value') else step.get('added', '')
        new_count = step.get('new_count', 0)
        count_change = step.get('count_change', 0)

        if action == 'initial':
            formatted_output.append(
                f"{idx:2d}. [{field:15s}] 🎯 Initial: '{value}' → {new_count:,} results"
            )
        elif action == 'added':
            if count_change >= 0:
                formatted_output.append(
                    f"{idx:2d}. [{field:15s}] ➕ Added: '{value}' → {new_count:,} results (change: +{count_change:,})"
                )
            else:
                formatted_output.append(
                    f"{idx:2d}. [{field:15s}] ➕ Added: '{value}' → {new_count:,} results (change: {count_change:,})"
                )
        elif action == 'removed':
            formatted_output.append(
                f"{idx:2d}. [{field:15s}] ➖ Removed: '{value}' → {new_count:,} results (change: {count_change:+,})"
            )
        elif action == 'tightened':
            formatted_output.append(
                f"{idx:2d}. [{field:15s}] 🔧 Tightened: Added '{value}' → {new_count:,} results (change: {count_change:+,})"
            )
        elif action == 'reduced_initial':
            formatted_output.append(
                f"{idx:2d}. [{field:15s}] ⬇️  Reduced: '{value}' → {new_count:,} results (change: {count_change:+,})"
            )
        elif action == 'rollback':
            formatted_output.append(
                f"{idx:2d}. [{field:15s}] 🔄 Rollback: '{value}' → {new_count:,} results (change: {count_change:+,})"
            )
        else:
            # Generic format for other actions
            formatted_output.append(
                f"{idx:2d}. [{field:15s}] {action}: '{value}' → {new_count:,} results"
            )

    formatted_output.append("-" * 60)
    return "\n".join(formatted_output)


def build_most_strict_conditions(initial_conditions: dict, relaxation_options: dict) -> dict:
    """
    Build the 'most strict' search conditions - most restrictive to get minimum candidates.
    Uses top 1 item (highest probability) for each filter.
    Skills are handled separately - uses top 2 skills.

    Args:
        initial_conditions: Original search conditions
        relaxation_options: Available options for each field

    Returns:
        Most restrictive conditions dict
    """
    most_strict = initial_conditions.copy()

    # Use top 1 location (highest probability - first in sorted list)
    if "location" in relaxation_options and relaxation_options["location"]:
        top_loc = relaxation_options["location"][0]
        if isinstance(top_loc, (list, tuple)) and len(top_loc) >= 1:
            loc_value = top_loc[0]
            if isinstance(loc_value, list):
                # Take only the first location from the list
                most_strict["location"] = {"name": [loc_value[0]] if loc_value else []}
            else:
                most_strict["location"] = {"name": [loc_value]}

    # Use top 1 seniority
    if "seniority" in relaxation_options and relaxation_options["seniority"]:
        top_sen = relaxation_options["seniority"][0]
        if isinstance(top_sen, (list, tuple)) and len(top_sen) >= 1:
            most_strict["seniority"] = [top_sen[0]]

    # Use top 1 job title
    if "job_title" in relaxation_options and relaxation_options["job_title"]:
        top_title = relaxation_options["job_title"][0]
        if isinstance(top_title, (list, tuple)) and len(top_title) >= 1:
            most_strict["job_title"] = [top_title[0]]

    # Use top 1 industry
    if "industry" in relaxation_options and relaxation_options["industry"]:
        top_ind = relaxation_options["industry"][0]
        if isinstance(top_ind, (list, tuple)) and len(top_ind) >= 1:
            most_strict["industry"] = [top_ind[0]]

    # Use top 1 job function
    if "job_function" in relaxation_options and relaxation_options["job_function"]:
        top_func = relaxation_options["job_function"][0]
        if isinstance(top_func, (list, tuple)) and len(top_func) >= 1:
            most_strict["job_function"] = [top_func[0]]

    return most_strict


def expand_year_of_experience_range(year_of_experience: dict, aggressive: bool = False) -> dict:
    """
    Expand year_of_experience range to be more permissive.

    Args:
        year_of_experience: Original year range dict with 'start_num_year' and 'end_num_year'
        aggressive: If True, use more aggressive expansion (for most tolerant conditions)
                   If False, use standard expansion (for normal fallback)

    Returns:
        Expanded year range dict with 'start_num_year' and 'end_num_year'
    """
    if not year_of_experience:
        return None

    original_start = year_of_experience.get("start_num_year", 0)
    original_end = year_of_experience.get("end_num_year", 0)

    if aggressive:
        # More aggressive expansion for most tolerant conditions
        # Reduce start by 2 (min 0) and increase end by max(end * 0.5, 3)
        expanded_start = min(max(0, original_start - 2), 29)
        expanded_end = min(original_end + int(max(original_end * 0.5, 3)), 29)
    else:
        # Standard expansion (same as normal process fallback)
        # Reduce start by 1 (min 0) and increase end by max(end * 0.5, 2)
        expanded_start = min(max(0, original_start - 1), 29)
        expanded_end = min(original_end + int(max(original_end * 0.5, 2)), 29)

    return {
        "start_num_year": expanded_start,
        "end_num_year": expanded_end
    }


def build_most_tolerant_conditions(initial_conditions: dict, relaxation_options: dict,
                                   allow_remove_industry: bool = True) -> dict:
    """
    Build the 'most tolerant' search conditions - most relaxed to get maximum candidates.
    Uses ALL items for location, seniority, job_title, language from relaxation_options (merged with initial).
    REMOVES industry and job_function filters entirely to maximize results.
    EXPANDS year_of_experience range aggressively to maximize results.
    Skills are handled separately - uses all skills.

    Args:
        initial_conditions: Original search conditions
        relaxation_options: Available options for each field

    Returns:
        Most tolerant/relaxed conditions dict
    """
    most_tolerant = initial_conditions.copy()

    # REMOVE job_function entirely for maximum tolerance.
    most_tolerant.pop("job_function", None)
    # Industry: remove for max tolerance, OR (when not allowed) broaden to ALL
    # industries from relaxation_options so the pool stays within the JD's industries.
    if allow_remove_industry:
        most_tolerant.pop("industry", None)
    else:
        all_inds = []
        for opt in relaxation_options.get("industry", []) or []:
            name = opt[0] if isinstance(opt, (list, tuple)) else opt
            if name and name not in all_inds:
                all_inds.append(name)
        for name in initial_conditions.get("industry", []) or []:
            if name and name not in all_inds:
                all_inds.append(name)
        if all_inds:
            most_tolerant["industry"] = all_inds
        else:
            most_tolerant.pop("industry", None)

    # NOTE: year_of_experience is kept as original value here
    # The expansion is done separately in extreme_condition_search for the search only

    # Collect ALL locations: start with initial_conditions, then add from relaxation_options
    all_locations = []
    # First, get locations from initial_conditions (use initial_conditions directly)
    if "location" in initial_conditions:
        initial_locs = initial_conditions.get("location", {})
        if isinstance(initial_locs, dict):
            initial_loc_names = initial_locs.get("name", [])
            if isinstance(initial_loc_names, list):
                all_locations.extend(initial_loc_names)
            elif initial_loc_names:
                all_locations.append(initial_loc_names)
    # Then add from relaxation_options
    if "location" in relaxation_options and relaxation_options["location"]:
        for loc_option in relaxation_options["location"]:
            if isinstance(loc_option, (list, tuple)) and len(loc_option) >= 1:
                loc_value = loc_option[0]
                if isinstance(loc_value, list):
                    all_locations.extend(loc_value)
                else:
                    all_locations.append(loc_value)
    # Deduplicate while preserving order
    if all_locations:
        seen = set()
        unique_locations = []
        for loc in all_locations:
            if loc not in seen:
                seen.add(loc)
                unique_locations.append(loc)
        most_tolerant["location"] = {"name": unique_locations}

    # Collect ALL seniority: start with initial_conditions, then add from relaxation_options
    all_seniority = []
    # First, get seniority from initial_conditions (use initial_conditions directly)
    if "seniority" in initial_conditions:
        initial_sen = initial_conditions.get("seniority", [])
        if isinstance(initial_sen, list):
            all_seniority.extend(initial_sen)
        elif initial_sen:
            all_seniority.append(initial_sen)
    # Then add from relaxation_options
    if "seniority" in relaxation_options and relaxation_options["seniority"]:
        for sen_option in relaxation_options["seniority"]:
            if isinstance(sen_option, (list, tuple)) and len(sen_option) >= 1:
                all_seniority.append(sen_option[0])
    # Deduplicate while preserving order
    if all_seniority:
        seen = set()
        unique_seniority = []
        for sen in all_seniority:
            if sen not in seen:
                seen.add(sen)
                unique_seniority.append(sen)
        most_tolerant["seniority"] = unique_seniority

    # Collect ALL job titles: start with initial_conditions, then add from relaxation_options
    all_titles = []
    # First, get job_title from initial_conditions (use initial_conditions directly)
    if "job_title" in initial_conditions:
        initial_titles = initial_conditions.get("job_title", [])
        if isinstance(initial_titles, list):
            all_titles.extend(initial_titles)
        elif initial_titles:
            all_titles.append(initial_titles)
    # Then add from relaxation_options
    if "job_title" in relaxation_options and relaxation_options["job_title"]:
        for title_option in relaxation_options["job_title"]:
            if isinstance(title_option, (list, tuple)) and len(title_option) >= 1:
                all_titles.append(title_option[0])
    # Deduplicate while preserving order
    if all_titles:
        seen = set()
        unique_titles = []
        for title in all_titles:
            if title not in seen:
                seen.add(title)
                unique_titles.append(title)
        most_tolerant["job_title"] = unique_titles

    # Collect ALL languages: start with initial_conditions, then add from relaxation_options
    all_languages = []
    # First, get language from initial_conditions (use initial_conditions directly)
    if "language" in initial_conditions:
        initial_langs = initial_conditions.get("language", [])
        if isinstance(initial_langs, list):
            all_languages.extend(initial_langs)
        elif initial_langs:
            all_languages.append(initial_langs)
    # Then add from relaxation_options
    if "language" in relaxation_options and relaxation_options["language"]:
        for lang_option in relaxation_options["language"]:
            if isinstance(lang_option, (list, tuple)) and len(lang_option) >= 1:
                lang_value = lang_option[0]
                if isinstance(lang_value, list):
                    all_languages.extend(lang_value)
                else:
                    all_languages.append(lang_value)
            elif isinstance(lang_option, str):
                all_languages.append(lang_option)
    # Deduplicate while preserving order
    if all_languages:
        seen = set()
        unique_languages = []
        for lang in all_languages:
            if lang not in seen:
                seen.add(lang)
                unique_languages.append(lang)
        most_tolerant["language"] = unique_languages

    # NOTE: industry and job_function are intentionally NOT added - they are removed above
    # This is to maximize the number of candidates returned

    return most_tolerant


def extreme_condition_search(initial_conditions: dict, mandatory_skills: list, relaxation_options: dict,
                              min_target: int = 200, tolerant_threshold: int = 25,
                              allow_remove_industry: bool = True) -> dict:
    """
    Run extreme condition searches at the beginning of optimization to potentially shortcut the process.

    Two extreme searches are performed:
    1. Most Strict: Top 1 item per filter (highest probability), top 2 skills
    2. Most Tolerant: All items for location/seniority/job_title, NO industry/job_function, all skills

    Decision logic:
    - If most_strict result >= min_target (200): Use most_strict as final (enough candidates with strict conditions)
    - Elif most_tolerant result < tolerant_threshold (25): Use most_tolerant as final (too few even with relaxed conditions)
    - Else: Return None to indicate normal optimization should proceed

    Args:
        initial_conditions: Original search conditions
        mandatory_skills: List of skills with probabilities [{"skill": "...", "probability": ...}, ...]
        relaxation_options: Available options for each field
        min_target: Minimum target result count (default 200) - if strict search exceeds this, use strict
        tolerant_threshold: Threshold for tolerant search (default 25) - if tolerant search below this, use tolerant

    Returns:
        dict with:
        - decision: "use_most_strict", "use_most_tolerant", or "continue_normal"
        - final_conditions: conditions to use (or None if continue_normal)
        - final_skills: skills list to use (or None if continue_normal)
        - final_count: result count (or None if continue_normal)
        - format_filter_conditions: API conditions (or None if continue_normal)
        - most_strict_count: count from strict search
        - most_tolerant_count: count from tolerant search
        - optimization_path_entry: log entry for the extreme search step
    """
    print("\n" + "=" * 90)
    print("EXTREME CONDITION SEARCH - Running boundary searches first")
    print("=" * 90)

    llm = get_global_llm()

    # Build the two extreme condition sets
    most_strict_conditions = build_most_strict_conditions(initial_conditions, relaxation_options)
    most_tolerant_conditions = build_most_tolerant_conditions(initial_conditions, relaxation_options,
                                                              allow_remove_industry=allow_remove_industry)

    # Create search conditions for most tolerant with expanded year_of_experience
    # The expanded version is used for search only, original is kept for final conditions
    most_tolerant_search_conditions = most_tolerant_conditions.copy()
    if "year_of_experience" in most_tolerant_search_conditions and most_tolerant_search_conditions["year_of_experience"]:
        expanded_yoe = expand_year_of_experience_range(most_tolerant_search_conditions["year_of_experience"], aggressive=True)
        if expanded_yoe:
            most_tolerant_search_conditions["year_of_experience"] = expanded_yoe
            print(f"\n   Year of Experience expanded for search: {most_tolerant_conditions.get('year_of_experience')} -> {expanded_yoe}")

    # Prepare skills for each search direction
    # Most strict: Use top 2 skills
    top_2_skills = [s["skill"] for s in mandatory_skills[:2]] if len(mandatory_skills) >= 2 else [s["skill"] for s in mandatory_skills]

    # Most tolerant: Use all skills
    all_skills = [s["skill"] for s in mandatory_skills]

    print(f"\n Search Conditions Summary:")
    print(f"   Most Strict: {len(top_2_skills)} skills (top 2), top 1 of each filter")
    print(f"   Most Tolerant: {len(all_skills)} skills (all), all options for each filter, expanded year_of_experience")

    print(f"\n   Most Strict Conditions: {most_strict_conditions}")
    print(f"   Most Tolerant Conditions (for final): {most_tolerant_conditions}")
    print(f"   Most Tolerant Search Conditions (with expanded YoE): {most_tolerant_search_conditions}")

    # Execute searches in parallel
    start_time = time.time()
    parallel_results = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(execute_parallel_search, most_strict_conditions, top_2_skills, "most_strict", llm): "most_strict",
            # Use expanded year_of_experience for search, but keep original conditions for return
            executor.submit(execute_parallel_search, most_tolerant_search_conditions, all_skills, "most_tolerant", llm): "most_tolerant",
        }

        for future in as_completed(futures):
            label = futures[future]
            try:
                result = future.result()
                parallel_results[result["label"]] = result
            except Exception as e:
                print(f"Error in {label} search: {e}")
                parallel_results[label] = {
                    "label": label,
                    "count": 0,
                    "conditions": {},
                    "format_filter_conditions": {},
                    "skills_list": [],
                    "error": str(e),
                }

    elapsed_time = time.time() - start_time
    print(f"\n Extreme search completed in {elapsed_time:.2f} seconds")

    # Accumulate tokens from both parallel searches
    _extreme_input_tokens = (
        parallel_results.get("most_strict", {}).get("input_tokens", 0)
        + parallel_results.get("most_tolerant", {}).get("input_tokens", 0)
    )
    _extreme_output_tokens = (
        parallel_results.get("most_strict", {}).get("output_tokens", 0)
        + parallel_results.get("most_tolerant", {}).get("output_tokens", 0)
    )

    # Extract counts
    most_strict_count = parallel_results.get("most_strict", {}).get("count", 0)
    most_tolerant_count = parallel_results.get("most_tolerant", {}).get("count", 0)

    print(f"\n Extreme Search Results:")
    print(f"   Most Strict (restrictive): {most_strict_count:,} results")
    print(f"   Most Tolerant (relaxed): {most_tolerant_count:,} results")

    # Decision logic
    decision = None
    final_conditions = None
    final_skills = None
    final_count = None
    format_filter_conditions = None
    optimization_path_entry = None

    if most_strict_count >= min_target:
        # Case 1: Most strict search returns >= min_target (200)
        # Use strict conditions as final - no need to optimize further
        decision = "use_most_strict"
        final_conditions = most_strict_conditions
        final_skills = top_2_skills
        final_count = most_strict_count
        format_filter_conditions = parallel_results.get("most_strict", {}).get("format_filter_conditions", {})
        optimization_path_entry = {
            "step": 0,
            "action": "extreme_search",
            "decision": f"Most strict search returned {most_strict_count} >= {min_target}, using strict conditions",
            "conditions_type": "most_strict",
            "count": most_strict_count,
            "most_tolerant_count": most_tolerant_count,
        }
        print(f"\n DECISION: Most strict returned {most_strict_count} (>= {min_target})")
        print(f"   Using MOST STRICT conditions as final (enough candidates with strict filters)")

    elif most_tolerant_count < tolerant_threshold:
        # Case 2: Most tolerant search returns < tolerant_threshold (25)
        # Use tolerant conditions as final - cannot get more candidates
        # Use the search conditions (with expanded year_of_experience) since that's what was actually searched
        decision = "use_most_tolerant"
        final_conditions = most_tolerant_search_conditions  # Use expanded year_of_experience
        final_skills = all_skills
        final_count = most_tolerant_count
        format_filter_conditions = parallel_results.get("most_tolerant", {}).get("format_filter_conditions", {})
        optimization_path_entry = {
            "step": 0,
            "action": "extreme_search",
            "decision": f"Most tolerant search returned {most_tolerant_count} < {tolerant_threshold}, using tolerant conditions with expanded year_of_experience",
            "conditions_type": "most_tolerant",
            "count": most_tolerant_count,
            "most_strict_count": most_strict_count,
        }
        print(f"\n DECISION: Most tolerant returned {most_tolerant_count} (< {tolerant_threshold})")
        print(f"   Using MOST TOLERANT conditions as final with expanded year_of_experience (cannot get more candidates)")

    else:
        # Neither condition met - proceed with normal optimization
        decision = "continue_normal"
        optimization_path_entry = {
            "step": 0,
            "action": "extreme_search",
            "decision": f"Neither extreme condition met (strict={most_strict_count}, tolerant={most_tolerant_count}), continuing normal optimization",
            "most_strict_count": most_strict_count,
            "most_tolerant_count": most_tolerant_count,
        }
        print(f"\n DECISION: Neither extreme condition applies")
        print(f"   Most strict: {most_strict_count} (< {min_target})")
        print(f"   Most tolerant: {most_tolerant_count} (>= {tolerant_threshold})")
        print(f"   Continuing with normal optimization...")

    return {
        "decision": decision,
        "final_conditions": final_conditions,
        "final_skills": final_skills,
        "final_count": final_count,
        "format_filter_conditions": format_filter_conditions,
        "most_strict_count": most_strict_count,
        "most_tolerant_count": most_tolerant_count,
        "optimization_path_entry": optimization_path_entry,
        "elapsed_time": elapsed_time,
        "input_tokens": _extreme_input_tokens,
        "output_tokens": _extreme_output_tokens,
    }


def execute_parallel_search(conditions: dict, skills_list: list, label: str, llm=None) -> dict:
    """
    Execute a single search with given conditions.
    Creates a new LLM instance per thread to avoid thread-safety issues with shared httpx.Client.
    Returns dict with label, count, and conditions.
    """
    try:
        # Create a thread-local LLM instance (httpx.Client is not thread-safe)
        thread_llm = ChatGPTWrapper()
        search_result = get_linkedin_search_num(conditions, skills_list, thread_llm)
        count = search_result.get("search_results_num", 0)
        format_filter_conditions = search_result.get("format_filter_conditions", {})
        print(f"\n🔍 [{label}] Search returned: {count} results")
        return {
            "label": label,
            "count": count,
            "conditions": conditions,
            "format_filter_conditions": format_filter_conditions,
            "skills_list": skills_list,
            "input_tokens": search_result.get("input_tokens", 0),
            "output_tokens": search_result.get("output_tokens", 0),
        }
    except Exception as e:
        print(f"\n❌ [{label}] Search failed: {e}")
        return {
            "label": label,
            "count": 0,
            "conditions": conditions,
            "format_filter_conditions": {},
            "skills_list": skills_list,
            "input_tokens": 0,
            "output_tokens": 0,
            "error": str(e),
        }


def single_process(initial_conditions, mandatory_skills, relaxation_options, min_target=200, max_target=600, tolerant_threshold=25, allow_remove_industry=True):
    """
    Single process optimization with extreme condition search at the beginning.

    Flow:
    1. Run extreme condition search (most strict & most tolerant)
    2. If most_strict >= min_target (200): Use strict conditions as final
    3. Elif most_tolerant < tolerant_threshold (25): Use tolerant conditions as final
    4. Else: Proceed with normal iterative optimization

    Args:
        initial_conditions: Initial search conditions
        mandatory_skills: List of skills with probabilities
        relaxation_options: Available options for each field
        min_target: Minimum target result count (default 200)
        max_target: Maximum target result count (default 600)
        tolerant_threshold: Threshold for tolerant search (default 25)

    Returns:
        dict with final_skills, final_conditions, optimization_path_log, etc.
    """
    print("=" * 90)
    print("LinkedIn Job Search Optimization Chatbot")
    print("=" * 90)

    # =========================================================================
    # STEP 0: Location expansion — expand non-metro locations to nearby cities
    # =========================================================================
    job_location_list = initial_conditions.get("location", {}).get("name", [])
    # Track total LLM token usage across all calls in this function
    _total_input_tokens  = 0
    _total_output_tokens = 0
    # Detect provider/model from the global LLM instance (ChatGPTWrapper → openai)
    _llm = get_global_llm()
    _llm_provider = "gemini" if "Gemini" in type(_llm).__name__ else "openai"
    _llm_model    = "gpt-4.1"  # default model used by ChatGPTWrapper.invoke()

    if job_location_list:
        location_expansion = expand_locations_for_optimization(job_location_list)
        _total_input_tokens  += location_expansion.get("input_tokens",  0)
        _total_output_tokens += location_expansion.get("output_tokens", 0)
        location_expansion = resolve_expanded_location_synonyms(location_expansion)
        location_relaxation = []
        if location_expansion["expanded_50"]:
            location_relaxation.append([location_expansion["expanded_50"], 0.7])
        if location_expansion["expanded_100"]:
            location_relaxation.append([location_expansion["expanded_100"], 0.2])
        if location_expansion["expanded_200"]:
            location_relaxation.append([location_expansion["expanded_200"], 0.1])
        if location_relaxation:
            relaxation_options["location"] = location_relaxation
            print(f"\n  Location relaxation options set: {len(location_relaxation)} tiers")

    # =========================================================================
    # STEP 1: Run extreme condition search first
    # =========================================================================
    extreme_result = extreme_condition_search(
        initial_conditions=initial_conditions,
        mandatory_skills=mandatory_skills,
        relaxation_options=relaxation_options,
        min_target=min_target,
        tolerant_threshold=tolerant_threshold,
        allow_remove_industry=allow_remove_industry
    )
    _total_input_tokens  += extreme_result.get("input_tokens",  0)
    _total_output_tokens += extreme_result.get("output_tokens", 0)

    extreme_decision = extreme_result.get("decision")

    # Check if we can shortcut the optimization process
    if extreme_decision == "use_most_strict":
        # Most strict search returned >= min_target, use it directly
        final_conditions = extreme_result.get("final_conditions", {})
        final_skills = extreme_result.get("final_skills", [])
        final_count = extreme_result.get("final_count", 0)
        format_filter_conditions = extreme_result.get("format_filter_conditions", {})

        # Build optimization path log
        optimization_path_entry = extreme_result.get("optimization_path_entry", {})
        readable_optimization_path = f"Extreme Search: Most strict conditions returned {final_count} results (>= {min_target}). Using strict conditions directly."

        # Rewrite for human readability
        skills_count = len(final_skills)
        skills_list_str = ', '.join(f'"{skill}"' for skill in final_skills)
        rewritten_optim_path = f"Started with {skills_count} mandatory skills for searching: {skills_list_str}\n\n"
        rewritten_optim_path += f"1. Ran extreme condition search with most strict filters (top 1 item per filter).\n"
        rewritten_optim_path += f"2. Most strict search returned {final_count:,} results, which exceeds the minimum target of {min_target}.\n"
        rewritten_optim_path += f"3. Using most strict conditions as final - no further optimization needed."

        print(f"\n Final Results Summary (Extreme Search - Most Strict):")
        print("-" * 60)
        print(f"Final result count: {final_count:,}")
        print(f"Final conditions: {final_conditions}")
        print(f"Final skills: {final_skills}")

        print("\n" + "=" * 90)
        print("Search Optimization Complete! (EXTREME SEARCH - MOST STRICT)")
        print("=" * 90)

        return {
            "final_skills": final_skills,
            "final_conditions": final_conditions,
            "optimization_path_log": readable_optimization_path,
            "rewritten_optim_path": rewritten_optim_path,
            "final_count": final_count,
            "format_filter_conditions": format_filter_conditions,
            "extreme_search_used": "most_strict",
            "total_input_tokens": _total_input_tokens,
            "total_output_tokens": _total_output_tokens,
            "llm_provider": _llm_provider,
            "llm_model": _llm_model,
        }

    elif extreme_decision == "use_most_tolerant":
        # Most tolerant search returned < tolerant_threshold, use it directly
        final_conditions = extreme_result.get("final_conditions", {})
        final_skills = extreme_result.get("final_skills", [])
        final_count = extreme_result.get("final_count", 0)
        format_filter_conditions = extreme_result.get("format_filter_conditions", {})

        # Build optimization path log
        optimization_path_entry = extreme_result.get("optimization_path_entry", {})
        readable_optimization_path = f"Extreme Search: Most tolerant conditions returned {final_count} results (< {tolerant_threshold}). Using tolerant conditions directly."

        # Rewrite for human readability
        skills_count = len(final_skills)
        skills_list_str = ', '.join(f'"{skill}"' for skill in final_skills)
        rewritten_optim_path = f"Started with {skills_count} mandatory skills for searching: {skills_list_str}\n\n"
        rewritten_optim_path += f"1. Ran extreme condition search with most tolerant filters (all items per filter).\n"
        rewritten_optim_path += f"2. Most tolerant search returned only {final_count:,} results, which is below the threshold of {tolerant_threshold}.\n"
        rewritten_optim_path += f"3. Using most tolerant conditions as final - cannot get more candidates even with relaxed filters."

        print(f"\n Final Results Summary (Extreme Search - Most Tolerant):")
        print("-" * 60)
        print(f"Final result count: {final_count:,}")
        print(f"Final conditions: {final_conditions}")
        print(f"Final skills: {final_skills}")

        print("\n" + "=" * 90)
        print("Search Optimization Complete! (EXTREME SEARCH - MOST TOLERANT)")
        print("=" * 90)

        return {
            "final_skills": final_skills,
            "final_conditions": final_conditions,
            "optimization_path_log": readable_optimization_path,
            "rewritten_optim_path": rewritten_optim_path,
            "final_count": final_count,
            "format_filter_conditions": format_filter_conditions,
            "extreme_search_used": "most_tolerant",
            "total_input_tokens": _total_input_tokens,
            "total_output_tokens": _total_output_tokens,
            "llm_provider": _llm_provider,
            "llm_model": _llm_model,
        }

    # =========================================================================
    # STEP 2: Neither extreme condition met - proceed with normal optimization
    # =========================================================================
    print("\n" + "=" * 90)
    print("Continuing with normal iterative optimization...")
    print("=" * 90)

    initial_state = {
        "current_conditions": initial_conditions,
        "mandatory_skills": mandatory_skills,
        "relaxation_options": relaxation_options,
        "current_count": 0,
        "format_filter_conditions": "",
        "job_skills_str": "",
        "user_response": "",
        "relaxation_history": [],
        "optimization_path_history": [],
        "next_action": "initial_search",
        "skills_used_count": 0,
        "auto_mode": True,  # Set to True to start in automatic mode
        "auto_relaxation_state": {},
        "min_target": min_target,  # Minimum target for result count (customizable, default: 200)
        "max_target": max_target,  # Maximum target for result count (customizable, default: 600)
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "allow_remove_industry": allow_remove_industry,  # if False, never drop the industry filter
    }

    graph = build_graph()

    thread_id = uuid.uuid4()
    print("thread_id:", thread_id)

    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 100
    }
    final_state = graph.invoke(initial_state, config)
    _total_input_tokens  += final_state.get("total_input_tokens", 0)
    _total_output_tokens += final_state.get("total_output_tokens", 0)

    # Get the optimization path and format it nicely
    optimization_path = final_state.get("optimization_path_history", [])
    final_conditions = final_state.get("current_conditions", {})
    final_count = final_state.get("current_count", 0)
    skills_used_count = int(final_state.get("skills_used_count", 0))
    mandatory_skills = final_state.get("mandatory_skills", [])
    final_skills = [record['skill'] for record in mandatory_skills[:skills_used_count]]

    # Add extreme search info to the beginning of optimization path
    extreme_search_info = (
        f"Extreme Search Pre-check:\n"
        f"  - Most Strict: {extreme_result.get('most_strict_count', 0)} results (threshold: >= {min_target})\n"
        f"  - Most Tolerant: {extreme_result.get('most_tolerant_count', 0)} results (threshold: < {tolerant_threshold})\n"
        f"  - Decision: Neither threshold met, proceeding with normal optimization\n\n"
    )

    # Display readable optimization path
    readable_optimization_path = extreme_search_info + format_optimization_path(optimization_path)

    rewrite_optimization_path_prompt = """
    You are given an optimization path log from a search system. Rewrite it into a correct, clear, concise and human-readable explanation.
    

    Optimization path log:
    {optimization_path_log}

    Requirements:
    1. Keep all the steps in the same order.
    2. Explain each step in clear and concise English, but make it more flexible for human read.
    3. Replace symbols/emojis with words (e.g., "Added", "Initial"). If log showing "+" filter, use term added, while if log shows "-" filter, use term removed.
    4. Clearly show:
      - What filter or change was applied with exact filter name with double quote.
      - Put number of change within quote.
      - How many results remained after that step.
      - How the result count changed compared to the previous step.
      - When Remove filter, mention there is too many number of results, or previous step dramatically increased results, need to revert back.
    5. Use a simple list or short paragraphs so a non-technical person can quickly understand how the search was narrowed or expanded.
    6. Do NOT add new steps or invent data that is not in the original log.

    Return only the rewritten explanation, without commentary about what you are doing.

    Example output:
    1. Started with an initial search using two skills, but found 0 results.
    2. Added the "Technical Leadership" skill filter, which increased the results to 14 (an increase of 14).
    3. Added the "System Architecture" skill filter, raising the results to 19 (an increase of 5).
    4. Added the "strategic" seniority filter, but the number of results stayed at 19 (no change).

    """

    llm = ChatGPTWrapper()
    prompt = rewrite_optimization_path_prompt.format(optimization_path_log=readable_optimization_path)
    rewritten_optim_path, input_tokens, output_tokens = llm.invoke(prompt, model_type="gpt-5-mini")
    _total_input_tokens  += input_tokens
    _total_output_tokens += output_tokens

    # Prepend final skills information to rewritten optimization path
    skills_count = len(final_skills)
    skills_list = ', '.join(f'"{skill}"' for skill in final_skills)
    final_skills_info = f"Started with {skills_count} mandatory skills for searching: {skills_list}\n\n"
    rewritten_optim_path = final_skills_info + rewritten_optim_path

    print("readable_optimization_path:", readable_optimization_path)
    print("rewritten_optim_path:", rewritten_optim_path)

    # Use format_filter_conditions from state (already computed during optimization)
    # No need to regenerate or re-verify - saves API calls
    format_filter_conditions = final_state.get("format_filter_conditions", {})

    # If format_filter_conditions is empty/missing, regenerate it (fallback only)
    if not format_filter_conditions or not format_filter_conditions.get("filters"):
        print("[INFO] format_filter_conditions not in state, regenerating...")
        linkedin_enum_params = get_linkedin_enum_data()
        format_filter_conditions = convert_filters_to_recruiter_api_conditions(
            final_conditions, final_skills, linkedin_enum_params, llm
        )
        _total_input_tokens  += format_filter_conditions.get("input_tokens",  0)
        _total_output_tokens += format_filter_conditions.get("output_tokens", 0)

    print("recruiter_conditions: ", str(format_filter_conditions))

    # Display final results summary
    print("\n📊 Final Results Summary:")
    print("-" * 60)
    print(f"Final result count: {final_count:,}")
    print(f"Final conditions: {final_conditions}")

    print("\n" + "=" * 90)
    print("Search Optimization Complete!")
    print("=" * 90)

    output_dict = {
        "final_skills": final_skills,
        "final_conditions": final_conditions,
        "optimization_path_log": readable_optimization_path,
        "rewritten_optim_path": rewritten_optim_path,
        "final_count": final_count,
        "format_filter_conditions": format_filter_conditions,
        "extreme_search_used": None,  # Neither extreme was used, normal optimization was performed
        "total_input_tokens": _total_input_tokens,
        "total_output_tokens": _total_output_tokens,
        "llm_provider": _llm_provider,
        "llm_model": _llm_model,
    }

    return output_dict


    # final_skills = ['RESTful API', 'Data Synchronization', 'Distributed Systems', 'Microservices']
    # final_conditions = {'industry': ['Technology, Information and Internet'], 'job_function': ['engineering'], 'job_title': ['Senior Backend Engineer', 'Backend Software Engineer'], 'location': {'name': ['CA', 'WA', 'OR', 'CO', 'TX']}, 'seniority': ['senior']}
    # rewritten_optim_path= """1. Started with an initial search "Started with 2 skills", which returned 139 results.
    # 2. Added the "Distributed Systems" filter, which increased the results to 170 ("+31").
    # 3. Added the "Microservices" filter, which increased the results to 223 ("+53")."""
    # final_count=223

    # linkedin_enum_params = get_linkedin_enum_data()
    #
    # llm = ChatGPTWrapper()
    #
    # recruiter_conditions = convert_filters_to_recruiter_api_conditions(
    #     final_conditions, final_skills, linkedin_enum_params, llm
    # )
    # print(str(recruiter_conditions))

    # # main()

    # ##############################################
    # # Batch processing
    # ##############################################
    # # batch_csv_filepath = "out/jd_parsing_result/auto_condition_optim_AI_70_cases.csv"
    # batch_csv_filepath = "graphs/out/jd_extracted_info_df.csv"
    # data_df = pd.read_csv(batch_csv_filepath)
    #
    # for idx, row in data_df.iterrows():
    #     print(f"=================== idx: {idx} =====================")
    #
    #     # if idx < 28 or idx > 70: continue
    #
    #     job_id = row.get("jd_id", None)
    #     job_desc = row["job_desc"]
    #
    #     # if int(job_id) not in [10113]: continue
    #
    #     if job_desc.strip() == "": continue
    #
    #     # if int(job_id) not in [9118, 155035, 7053, 7939]: continue
    #
    #     industry = row['company_industry']
    #     job_function = row['job_function']
    #     job_title = None
    #     job_location = row['job_location_list']
    #     seniority = row['job_seniority']
    #
    #     industry = ast.literal_eval(industry)
    #     job_function = ast.literal_eval(job_function)
    #     job_location = ast.literal_eval(job_location)
    #     seniority = ast.literal_eval(seniority)
    #
    #     job_function_info = row['job_function_info']
    #     job_title_info = row['job_title_info']
    #     job_seniority_info = row['job_seniority_info']
    #     job_required_language = row['job_required_language']
    #
    #     company_industry_info = row['company_industry_info']
    #     job_location_list = row['job_location_list']
    #     job_location_list = ast.literal_eval(job_location_list)
    #
    #     job_function_dict = ast.literal_eval(job_function_info)
    #     job_title_dict = ast.literal_eval(job_title_info)
    #     job_seniority_dict = ast.literal_eval(job_seniority_info)
    #     year_of_experience_options = job_seniority_dict['year_of_experience']
    #     company_industry_dict = ast.literal_eval(company_industry_info)
    #
    #     job_title = job_title_dict['title'][0][0] if len(job_title_dict['title']) > 0 else ""
    #     if job_title.strip() == "": continue
    #
    #     # Use the seniority from job_seniority (already parsed above)
    #     # Calculate year_of_experience by combining all seniority values
    #     initial_seniority = seniority if isinstance(seniority, list) else [seniority] if seniority else []
    #     initial_year_of_experience = None
    #     if initial_seniority and year_of_experience_options:
    #         # Use combine_seniority_year_ranges to get the combined year range for all seniority values
    #         initial_year_of_experience = combine_seniority_year_ranges(initial_seniority, year_of_experience_options)
    #
    #     initial_conditions = {
    #         'industry': industry,
    #         'job_function': job_function,
    #         'job_title': [job_title],
    #         'location': {"name": job_location_list},
    #         'language': job_required_language,
    #         'seniority': initial_seniority,  # Use seniority from job_seniority
    #         'year_of_experience': initial_year_of_experience  # Calculated from all seniority values
    #     }
    #
    #     mandatory_skills = row['mandatory_skills']
    #     mandatory_skills = ast.literal_eval(mandatory_skills)
    #
    #     initial_skills = [record['skill'] for record in mandatory_skills[:2]]
    #
    #     relaxation_options = {
    #         "job_function": job_function_dict['function'],
    #         "job_title": job_title_dict['title'],
    #         "seniority": year_of_experience_options,  # Store as seniority for proper mapping
    #         "industry": company_industry_dict['industry'],
    #         "location": job_location_list,
    #     }
    #
    #     print("\tStart search condition optimization")
    #     # try:
    #     #     final_conditions, readable_optimization_path, final_count = single_process(initial_conditions,
    #     #                                                                                mandatory_skills,
    #     #                                                                                relaxation_options)
    #     #     error = None
    #     # except Exception as e:
    #     #     error = str(e)
    #     #     final_conditions = initial_conditions
    #     #     readable_optimization_path = None
    #     #     final_count = 0
    #
    #     result_info = single_process(initial_conditions, mandatory_skills, relaxation_options)
    #
    #     final_skills = result_info.get('final_skills', None)
    #     final_conditions = result_info.get('final_conditions', None)
    #     optimization_path_log = result_info.get('optimization_path_log', None)
    #     readable_optim_path = result_info.get('rewritten_optim_path', None)
    #     format_filter_conditions = result_info.get('format_filter_conditions', None)
    #     final_count = result_info.get('final_count', 0)
    #
    #
    #     error = None
    #
    #     output_list = [[job_id, job_desc, initial_conditions, initial_skills, relaxation_options, format_filter_conditions,
    #                     final_conditions, final_skills, optimization_path_log, readable_optim_path, final_count, error]]
    #     output_df = pd.DataFrame(output_list,
    #                              columns=["job_id", "job_desc", "initial_conditions", "initial_skills",
    #                                       "relaxation_options", "format_filter_conditions",
    #                                       "final_conditions", "final_skills", "optimization_path_log", "readable_optimization_path",
    #                                       "final_count", "error"])
    #
    #     out_dir = "out/search_optimization_result"
    #     os.makedirs(out_dir, exist_ok=True)
    #     output_path = os.path.join(out_dir, f"search_opt_{job_id}.csv")
    #     output_df.to_csv(output_path)

# The copied base `single_process` (count-band optimizer, used per archetype). The
# multi-archetype `single_process` defined at the bottom of this file overrides the name.
_base_single_process = single_process


# Per-archetype "max evaluate candidates" — INCREASED 200 -> 500.
MAX_CANDIDATES_PER_ARCHETYPE = int(os.getenv("ASO_V3_MAX_PER_ARCHETYPE", "500"))
# How many archetypes to run (baseline + alternatives).
MAX_ARCHETYPES = int(os.getenv("ASO_V3_MAX_ARCHETYPES", "5"))
# How many archetypes to process CONCURRENTLY (field-extraction + per-archetype optimization
# run in parallel threads). Default = MAX_ARCHETYPES so all archetypes run at once.
# Concurrency for field-extraction + per-archetype optimization. Default 10 so all archetypes
# (and the JD-driven extraction phase) overlap; capped at the actual archetype count per run.
_ARCHETYPE_WORKERS = int(os.getenv("ASO_V3_ARCHETYPE_WORKERS", "10"))

# (v3 / v3.8) Low-yield relaxation threshold: if an archetype's optimized count is BELOW this,
# re-optimize it with geography widened + industry dropped + language stripped, and keep the
# broader result if it yields more. 0 disables (== v3 behavior). Default = min_target.
ASO_V3_LOW_YIELD_RELAX = os.getenv("ASO_V3_LOW_YIELD_RELAX")  # int or None -> use min_target

# (v3 fix #1/#2) BINDING-SKILL ENFORCEMENT — the count-band optimizer treats skills as SOFT
# (required=False) preferences and drops the most discriminating one to hit its target count,
# so niche roles (e.g. "friction-material" expertise) surface only generic same-family
# candidates. When ENABLED, the DEFINING concept is enforced as a hard recruiter KEYWORD
# filter — an OR-set of adjacent terms (#2), e.g. ("Friction Materials" OR "Brake Pads" OR
# Clutch OR Tire) — so a profile MENTIONING any of them (incl. friction-ADJACENT people like a
# tire-R&D engineer) still matches, while truly-unrelated candidates are dropped. The count is
# accepted even below min_target (relevance over volume) as long as it clears ASO_V3_MIN_RELEVANT;
# below that floor it falls back to the soft/widened result so a thin market still returns people.
#
# DEFAULT OFF (#1): the dynamic-scoring MUST-HAVE GATE (evaluation_graph_v2) now handles
# precision downstream WITHOUT excluding adjacent candidates from the search, so hard search
# enforcement is opt-in. Set ASO_V3_REQUIRE_BINDING_SKILLS>0 (or the app toggle) to turn it on.
ASO_V3_REQUIRE_BINDING_SKILLS = int(os.getenv("ASO_V3_REQUIRE_BINDING_SKILLS", "0"))  # 0 disables
ASO_V3_MIN_RELEVANT = int(os.getenv("ASO_V3_MIN_RELEVANT", "5"))
# (Phase-2 speed) Only the top-K archetypes (by derivation priority: baseline + most-likely
# personas) are eligible for the expensive LOW-YIELD WIDEN re-optimization. The widen doubles a
# starved persona's count-check chain and — because it's the slowest chain — sets Phase-2 wall
# time. Capping it to the top-K skips the widen rescue for niche/low-likelihood personas.
# Default 5 = widen the top-5 archetypes (with MAX_ARCHETYPES=5 this covers all of them, i.e.
# the original widen-all behavior). Set ASO_V3_WIDEN_TOP_K=3 to widen only the top 3.
ASO_V3_WIDEN_TOP_K = int(os.getenv("ASO_V3_WIDEN_TOP_K", "5"))

# Conditions-dict keys that hold a flat list of names (union-merged across archetypes).
_LIST_KEYS = ("industry", "job_function", "seniority", "language", "job_title", "degree")


# ===========================================================================
# JD-TEXT-DRIVEN ARCHETYPES
# Ported from search_optimization_v3.6/archetype_pipeline.py and
# experiments/advanced_search_optimization_v3_deprecated.py so single_process can derive
# GENUINELY DISTINCT archetypes (each with its own LLM-extracted + typeahead-verified
# search fields) from the JD text — instead of merely permuting the parsed top
# title/industry, which the optimizer's relaxation collapses back into one search.
# ===========================================================================
import ast as _ast
import re as _re
import asyncio as _asyncio

# Same graphs_v2-first import policy as the top of the module.
try:
    from graphs_v2.llms.chatgpt import ChatGPTWrapper as _ChatGPTWrapper
except ImportError:
    from llms.chatgpt import ChatGPTWrapper as _ChatGPTWrapper
try:
    from graphs_v2.utils.general_utils import clean_text as _clean_text, extract_json_block as _extract_json_block
except ImportError:
    from utils.general_utils import clean_text as _clean_text, extract_json_block as _extract_json_block
try:
    from graphs_v2.utils.synonym_association import (
        Synonym_Associations as _SynAssoc,
        get_synonym_associations_async as _get_syn_async,
        pick_best_geo_synonym as _pick_best_geo,
    )
except ImportError:
    from utils.synonym_association import (
        Synonym_Associations as _SynAssoc,
        get_synonym_associations_async as _get_syn_async,
        pick_best_geo_synonym as _pick_best_geo,
    )
try:
    from graphs_v2.prompts import (
        extract_job_location_prompt as _P_LOC,
        extract_job_function_prompt as _P_FUNC,
        analyze_job_seniority_prompt as _P_SEN,
        jd_extraction_language_template as _P_LANG,
        extract_jd_experience_requirement as _P_YEAR,
        extract_job_title_prompt as _P_TITLE,
        extract_company_industry_prompt as _P_IND,
        job_skills_understanding_prompt as _P_SKILL,
    )
except ImportError:
    from prompts import (
        extract_job_location_prompt as _P_LOC,
        extract_job_function_prompt as _P_FUNC,
        analyze_job_seniority_prompt as _P_SEN,
        jd_extraction_language_template as _P_LANG,
        extract_jd_experience_requirement as _P_YEAR,
        extract_job_title_prompt as _P_TITLE,
        extract_company_industry_prompt as _P_IND,
        job_skills_understanding_prompt as _P_SKILL,
    )

_V3_FIELD_MODEL = os.getenv("ASO_V3_FIELD_MODEL", "gpt-4.1")

# LinkedIn enums (fixed allowed labels) for enum-constrained fields.
_LINKEDIN_JOB_FUNCTIONS = [
    "Accounting", "Administrative", "Arts and Design", "Business Development",
    "Community and Social Services", "Consulting", "Education", "Engineering",
    "Entrepreneurship", "Finance", "Healthcare Services", "Human Resources",
    "Information Technology", "Legal", "Marketing", "Media and Communication",
    "Military and Protective Services", "Operations", "Product Management",
    "Program and Project Management", "Purchasing", "Quality Assurance",
    "Real Estate", "Research", "Sales", "Customer Success and Support",
]
_LINKEDIN_SENIORITY = [
    "Owner / Partner", "CXO", "Vice President", "Director", "Experienced Manager",
    "Entry Level Manager", "Strategic", "Senior", "Entry Level", "In Training",
]


def _load_linkedin_industries() -> list:
    """The canonical LinkedIn industry allow-list, lifted from extract_company_industry_prompt."""
    m = _re.search(r"\[[^\[\]]+\]", _P_IND, _re.DOTALL)
    if not m:
        return []
    try:
        return [str(v) for v in _ast.literal_eval(m.group(0))]
    except Exception:
        return []


_LINKEDIN_INDUSTRIES = _load_linkedin_industries()

# 6-suggestion variants of the parsing-graph prompts (same logic, more fallbacks).
_JOB_TITLE_PROMPT_6 = _P_TITLE.replace("top 3 job titles", "top 6 job titles")
_SKILLS_PROMPT_6 = _P_SKILL.replace(
    "top 4 mandatory must have skills", "top 6 mandatory must have skills"
)
_INDUSTRY_PROMPT_6 = _P_IND.replace(
    "Choose exactly one industry from:",
    "Choose the 6 most likely industries (return exactly 6, ordered most→least likely) from:",
)
_TOP_N_SUGGESTIONS = 6

_DEGREE_PROMPT = """
    Task: From the job description, extract the required education DEGREE level(s),
    ordered high→low by likelihood with probability (normalized to 1). Use standard
    LinkedIn degree names, e.g. "Bachelor's degree", "Master's degree",
    "Doctor of Philosophy - PhD", "Associate's degree". If the JD states a field
    only (e.g. "BS in Computer Science"), output the degree LEVEL
    ("Bachelor's degree"). If no degree is required, return [].

    Output (Markdown JSON block only; no extra text):
    ```json
    {{ "degree": [["<degree>", probability], ...] }}
    ```

    Job Description:
    {job_desc}
"""


# (v3 fix #4) The structured "Required Skills" field is often GENERIC (e.g. 测试流程/testing,
# 制造/manufacturing) while the DEFINING requirement lives only in the free-text body /
# "Job Requirements" (任职资格要求). This prompt mines the WHOLE JD for the few role-defining
# must-haves and returns SEARCHABLE English LinkedIn skill names (synonyms) for each, so a
# niche or non-English term still resolves in LinkedIn's skill typeahead and can be ENFORCED.
_BINDING_SKILLS_PROMPT = """You are an expert technical recruiter. Read the ENTIRE job
description, INCLUDING the free-text responsibilities and the "Job Requirements" /
"任职资格要求" section — NOT only the structured "Required Skills" field.

Identify the 1-3 DEFINING, role-specific MUST-HAVE skills or domain knowledge that truly
separate a qualified candidate from a generic one in the same job family. IGNORE generic
skills most candidates in that field already share (e.g. "manufacturing", "testing",
"communication", "project management").

For EACH defining skill, give 2-4 SEARCHABLE LinkedIn skill names in ENGLISH that a real
profile would actually list (synonyms / industry terms), so a niche or non-English term still
resolves in LinkedIn's skill typeahead. Example: 摩擦片 (friction plate) ->
["Friction Materials", "Brake Pads", "Brake Linings", "Clutch"].

Output (Markdown JSON block only; no extra text):
```json
{{"binding_skills": [{{"concept": "<short>", "candidates": ["<EN skill>", "..."], "importance": <0-1>}}]}}
```
If the role has no defining/niche must-have (a truly generic role), return {{"binding_skills": []}}.

Job Description:
{job_desc}
"""


# ---- typeahead verification (parallel) ----
async def _safe_synonyms(category, terms):
    terms = [_clean_text(t) for t in terms if t]
    if not terms:
        return {}
    try:
        return await _get_syn_async(category, terms, max_concurrent=5)
    except Exception as e:
        print(f"  [v3] typeahead {category.value} unavailable: {e}")
        return None


def _verify_from_map(category, term, term_map) -> dict:
    if term_map is None:
        return {"linkedin": None, "exists": None, "candidates": []}
    cands = term_map.get(_clean_text(term), [])
    if not cands:
        return {"linkedin": None, "exists": False, "candidates": []}
    if category == _SynAssoc.LOCATION:
        best = _pick_best_geo(term, cands)
    else:
        tnorm = _clean_text(term).lower()
        best = next((c for c in cands if _clean_text(c).lower() == tnorm), cands[0])
    return {"linkedin": best, "exists": True, "candidates": cands}


async def _ainvoke_json(llm, prompt, model_type) -> dict:
    raw, _, _ = await llm.ainvoke(prompt, model_type=model_type, temperature=0.1)
    try:
        return _extract_json_block(raw)
    except Exception as e:
        print(f"  [v3] JSON parse failed: {e}")
        return {}


def _prob_pairs(items) -> list:
    prob_keys = {"prob", "probability"}
    out = []
    for it in items or []:
        if isinstance(it, dict):
            prob = next((it[k] for k in ("prob", "probability") if k in it), None)
            name = next((v for k, v in it.items() if k.lower() not in prob_keys), None)
            if name is not None:
                out.append((str(name), prob))
        elif isinstance(it, (list, tuple)) and it:
            out.append((str(it[0]), it[1] if len(it) > 1 else None))
        elif isinstance(it, str):
            out.append((it, None))
    return out


def _enum_pairs(pairs, allowed) -> list:
    lut = {a.lower(): a for a in allowed}
    out = []
    for name, prob in pairs:
        canon = lut.get(str(name).strip().lower())
        if canon and not any(o["name"] == canon for o in out):
            out.append({"name": canon, "prob": prob})
    return out


async def _extract_search_fields_async(job_desc, llm, model_type) -> dict:
    prompts = {
        "location": _P_LOC.format(job_desc=job_desc),
        "job_function": _P_FUNC.format(job_desc=job_desc),
        "seniority": _P_SEN.format(job_desc=job_desc),
        "language": _P_LANG.format(job_desc=job_desc),
        "year": _P_YEAR.format(job_desc=job_desc),
        "title": _JOB_TITLE_PROMPT_6.format(job_desc=job_desc),
        "industry": _INDUSTRY_PROMPT_6.format(job_desc=job_desc),
        "skills": _SKILLS_PROMPT_6.format(job_desc=job_desc),
        "degree": _DEGREE_PROMPT.format(job_desc=job_desc),
        "binding": _BINDING_SKILLS_PROMPT.format(job_desc=job_desc),   # (v3 fix #4)
    }
    keys = list(prompts)
    raws = await _asyncio.gather(*[_ainvoke_json(llm, prompts[k], model_type) for k in keys])
    parsed = dict(zip(keys, raws))

    locations = parsed["location"].get("job_location") or []
    if isinstance(locations, str):
        locations = [locations]
    job_function_out = _enum_pairs(_prob_pairs(parsed["job_function"].get("function")), _LINKEDIN_JOB_FUNCTIONS)
    seniority_out = _enum_pairs(_prob_pairs(parsed["seniority"].get("seniority")), _LINKEDIN_SENIORITY)
    language_out = [str(x) for x in (parsed["language"].get("job_required_language") or [])]
    yr = parsed["year"]
    year_of_experience = {
        "min_years": yr.get("suggest_min_year", yr.get("min_year")),
        "max_years": yr.get("suggest_max_year", yr.get("max_year")),
    }
    title_pairs = _prob_pairs(parsed["title"].get("title"))[:_TOP_N_SUGGESTIONS]
    industry_pairs = _prob_pairs(parsed["industry"].get("industry"))[:_TOP_N_SUGGESTIONS]
    skill_pairs = _prob_pairs(parsed["skills"].get("mandatory_skills"))[:_TOP_N_SUGGESTIONS]
    degree_pairs = _prob_pairs(parsed["degree"].get("degree"))[:4]

    # (v3 fix #4) DEFINING must-have skills mined from the whole JD, each with English LinkedIn
    # skill-name fallbacks for typeahead. We verify ALL candidates and keep the first that
    # resolves, so a niche / non-English term (e.g. 摩擦片) still becomes a searchable facet.
    binding_specs = []
    for b in (parsed["binding"].get("binding_skills") or []):
        if isinstance(b, dict):
            cands = [str(c).strip() for c in (b.get("candidates") or []) if str(c).strip()]
            if cands:
                binding_specs.append({"candidates": cands,
                                      "importance": float(b.get("importance") or 0.9)})
    binding_all = [c for spec in binding_specs for c in spec["candidates"]]

    loc_map, title_map, ind_map, skill_map, degree_map, bind_map = await _asyncio.gather(
        _safe_synonyms(_SynAssoc.LOCATION, locations),
        _safe_synonyms(_SynAssoc.OCCUPATION, [n for n, _ in title_pairs]),
        _safe_synonyms(_SynAssoc.INDUSTRY, [n for n, _ in industry_pairs]),
        _safe_synonyms(_SynAssoc.SKILL, [n for n, _ in skill_pairs]),
        _safe_synonyms(_SynAssoc.DEGREE, [n for n, _ in degree_pairs]),
        _safe_synonyms(_SynAssoc.SKILL, binding_all),
    )

    location_out = [{"raw": loc, **_verify_from_map(_SynAssoc.LOCATION, loc, loc_map)}
                    for loc in locations]
    title_out = [{"title": n, "prob": p, **_verify_from_map(_SynAssoc.OCCUPATION, n, title_map)}
                 for n, p in title_pairs]
    allow_lower = {i.lower() for i in _LINKEDIN_INDUSTRIES}
    industry_out = []
    for n, p in industry_pairs:
        v = _verify_from_map(_SynAssoc.INDUSTRY, n, ind_map)
        v["in_allowlist"] = n.lower() in allow_lower
        industry_out.append({"industry": n, "prob": p, **v})
    skills_out = [{"skill": n, "prob": p, **_verify_from_map(_SynAssoc.SKILL, n, skill_map)}
                  for n, p in skill_pairs]
    degree_out = [{"degree": n, "prob": p, **_verify_from_map(_SynAssoc.DEGREE, n, degree_map)}
                  for n, p in degree_pairs]

    # (v3 fix #4) Prepend each defining skill (first candidate that resolves in typeahead),
    # flagged binding=True with a top probability so it is the one ENFORCED as required (fix #1).
    seen_skill = {str(s.get("linkedin") or s.get("skill") or "").strip().lower() for s in skills_out}
    binding_out = []
    for spec in binding_specs:
        chosen = None
        for cand in spec["candidates"]:
            v = _verify_from_map(_SynAssoc.SKILL, cand, bind_map)
            if v.get("exists") and v.get("linkedin"):
                chosen = {"skill": cand, "prob": max(0.95, spec["importance"]),
                          "binding": True, **v}
                break
        if not chosen:
            continue
        key = str(chosen.get("linkedin") or chosen["skill"]).strip().lower()
        if key in seen_skill:
            # already extracted as a normal skill -> just flag it binding + boost it
            for s in skills_out:
                if str(s.get("linkedin") or s.get("skill") or "").strip().lower() == key:
                    s["binding"] = True
                    s["prob"] = max(float(s.get("prob") or 0), chosen["prob"])
            continue
        seen_skill.add(key)
        binding_out.append(chosen)
    skills_out = binding_out + skills_out      # binding skills first (top probability)

    # (v3 fix #2) OR-groups of adjacent terms per defining concept, for the recruiter KEYWORD
    # enforcement (free-text, so non-typeahead/adjacent terms still match). One group per
    # concept; the enforcer ORs within a group and ANDs across groups.
    binding_keyword_groups = [
        _dedup([t for t in spec["candidates"] if t]) for spec in binding_specs
    ]
    binding_keyword_groups = [g for g in binding_keyword_groups if g]

    return {
        "location": location_out, "job_function": job_function_out, "seniority": seniority_out,
        "language": language_out, "year_of_experience": year_of_experience,
        "job_title": title_out, "industry": industry_out,
        "mandatory_skills": skills_out, "degree": degree_out,
        "binding_keyword_groups": binding_keyword_groups,
    }


def extract_binding_keyword_groups(job_desc, llm=None, model_type=_V3_FIELD_MODEL) -> list:
    """Mine the DEFINING must-have concepts from the JD in ONE LLM call -> list of OR-groups of
    English keyword synonyms (e.g. [["Friction Materials","Brake Pads","Clutch"]]). Free-text
    keywords, so NO typeahead needed. Call this ONCE per JD and pass the result into
    single_process(binding_keyword_groups=...) to enforce in fast CONDITIONS-ONLY mode — instead
    of re-deriving personas per archetype via the slow JD-driven path."""
    llm = llm or get_global_llm()
    try:
        raw, _, _ = llm.invoke(_BINDING_SKILLS_PROMPT.format(job_desc=job_desc),
                               model_type=model_type, temperature=0.1)
        data = _extract_json_block(raw)
    except Exception as e:
        print(f"  [v3] binding keyword extraction failed ({e}).")
        return []
    groups = []
    for b in (data.get("binding_skills") or []) if isinstance(data, dict) else []:
        if isinstance(b, dict):
            cands = _dedup([str(c).strip() for c in (b.get("candidates") or []) if str(c).strip()])
            if cands:
                groups.append(cands)
    return groups


def extract_search_fields(job_desc, llm, model_type=_V3_FIELD_MODEL) -> dict:
    """Extract LinkedIn search fields (parsing-graph prompts; 6 suggestions for
    title/industry/skills; typeahead existence verification). Sync wrapper."""
    return _asyncio.run(_extract_search_fields_async(job_desc, llm, model_type))


# ---- archetype generation + conversion to optimizer inputs ----
_ARCHETYPE_PROMPT = """You are an expert technical recruiter. Read the job description and infer the 3-5 \
DISTINCT, mutually-exclusive kinds of candidate (archetypes / personas) that companies posting this JD \
TYPICALLY hire in practice — realistic profiles of people who actually get the role, not a restatement of \
the requirements.

For each archetype give:
- "profile": a short title (e.g. "Senior Backend Engineer from a high-scale consumer product").
- "likelihood": the share of likelihood that a hired candidate matches THIS archetype rather than the \
others. Treat archetypes as mutually exclusive: the likelihoods across all archetypes MUST sum to 1.0.
- "rationale": one sentence on why this archetype fits the JD (signals: seniority, domain, stack, scope).
- "typical_background": 3-6 comma-separated traits (typical title, years, industry, key skills, company type).
- "industry_locked": true if a candidate from a DIFFERENT industry would be a POOR fit for this archetype \
(the role/domain is industry-specific, e.g. payments/fintech, medical devices, gaming), so the search must \
NOT broaden across industries; false if strong candidates can come from many industries (e.g. a generalist \
backend engineer or recruiter).

Order most to least likely. Return ONLY JSON:
{{"archetypes": [{{"profile": "...", "likelihood": 0.0, "rationale": "...", "typical_background": "...", "industry_locked": false}}]}}

Job description:
{job_desc}
"""


def generate_archetypes(llm, model, jd) -> list:
    """Return up to 5 archetypes sorted by likelihood, normalized to sum to 1.0."""
    try:
        raw, _, _ = llm.invoke(_ARCHETYPE_PROMPT.format(job_desc=jd), model_type=model, temperature=0.1)
        data = _extract_json_block(raw)
        items = data.get("archetypes", []) if isinstance(data, dict) else []
    except Exception:
        return []
    out = []
    for a in items:
        if not isinstance(a, dict) or not a.get("profile"):
            continue
        try:
            lk = float(a.get("likelihood"))
        except (TypeError, ValueError):
            lk = None
        out.append({"profile": str(a["profile"]), "likelihood": lk,
                    "rationale": str(a.get("rationale", "")),
                    "typical_background": str(a.get("typical_background", "")),
                    "industry_locked": bool(a.get("industry_locked", False))})
    out.sort(key=lambda x: (x["likelihood"] is not None, x["likelihood"] or 0), reverse=True)
    out = out[:5]
    total = sum(x["likelihood"] for x in out if isinstance(x["likelihood"], (int, float)) and x["likelihood"] > 0)
    if total > 0:
        for x in out:
            x["likelihood"] = (x["likelihood"] / total) if isinstance(x["likelihood"], (int, float)) else None
    return out


def baseline_archetype() -> dict:
    """A synthetic 'archetype' representing the WHOLE JD with NO persona bias — fields are
    extracted directly from the JD. Run it ALONGSIDE the personas so the union always
    includes the focused JD-center that persona searches over-narrow or over-dilute."""
    return {"profile": "JD baseline (whole-JD, no persona)", "likelihood": None,
            "rationale": "Direct extraction from the full JD — the focused center.",
            "typical_background": "", "is_baseline": True}


def _archetype_jd(jd, archetype) -> str:
    if archetype.get("is_baseline"):
        return jd
    return (f"{jd}\n\n---\nTARGET CANDIDATE PROFILE for this search: {archetype.get('profile', '')}.\n"
            f"Typical background: {archetype.get('typical_background', '')}.\n"
            f"Extract the search fields (job titles, industries, skills, seniority, location) that best "
            f"target THIS kind of candidate.")


def _required_languages_from_text(jd_text):
    """Parse the JD/form's explicit 'Required Languages:' line (AUTHORITATIVE over LLM
    body inference). Returns a list (possibly empty) if the line exists, else None."""
    m = _re.search(r"Required Languages:\s*([^\n]*)", jd_text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw or raw.lower() in ("none", "n/a", "na", "-", "any"):
        return []
    return [x.strip() for x in _re.split(r"[;,/]", raw) if x.strip()]


_RS = None


def _get_rs():
    global _RS
    if _RS is None:
        from linkedin_recruiter_apiservice.api_service import RecruiterService
        _RS = RecruiterService()
    return _RS


def _resolve_best_locations(fields):
    """v3.6 parity (batch_run.resolve_best_locations): among each location's typeahead
    candidates, pick the one with the LARGEST recruiter search count — broadens a small
    metro (e.g. 'Kota Kinabalu Metropolitan Area') up to its region/country so the optimizer
    doesn't narrow the pool to zero. Mutates + returns fields; no-op if the API is down."""
    locs = fields.get("location") or []
    if not locs:
        return fields
    try:
        rs = _get_rs()
    except Exception as e:
        print(f"  [v3-jd] RecruiterService unavailable for location broadening: {e}")
        return fields
    for it in locs:
        cands = [c for c in (it.get("candidates") or []) if c]
        if it.get("linkedin") and it["linkedin"] not in cands:
            cands.insert(0, it["linkedin"])
        if len(cands) <= 1:
            continue
        best, best_n = it.get("linkedin"), -1
        for c in cands:
            try:
                n = rs.get_search_num({"filters": {"locations": [
                    {"name": c, "required": False, "selected": True, "negated": False}]}}).get("num")
            except Exception:
                n = None
            if isinstance(n, int) and n > best_n:
                best, best_n = c, n
        if best:
            it["linkedin"], it["exists"] = best, True
    return fields


def extract_archetype_fields(jd, archetype, llm, model) -> dict:
    """LLM-extract + typeahead-verify LinkedIn search fields for one archetype. The JD's
    explicit 'Required Languages:' (when present) OVERRIDES the LLM body-inferred languages.
    Locations are broadened to the highest-coverage verified candidate (v3.6 parity)."""
    fields = extract_search_fields(_archetype_jd(jd, archetype), llm, model_type=model)
    req = _required_languages_from_text(jd)
    if req is not None:
        fields["language"] = req
    _resolve_best_locations(fields)
    return fields


def allow_remove_industry_for(archetype, global_keep_industry=False) -> bool:
    """Effective allow_remove_industry: industry filter is KEPT (not removable) when the
    caller globally keeps it OR this archetype is industry_locked."""
    return not (global_keep_industry or bool(archetype.get("industry_locked", False)))


_WIDEN_GEO_PROMPT = """The current search location(s) are: {locs}

To widen a talent search for a THIN LOCAL market, return BROADER geographic regions — but STRICTLY
WITHIN THE SAME country/countries as the input location(s). NEVER include a location in a DIFFERENT
country than the input (do NOT suggest nearby / neighboring countries or relocation sources).
Rules:
- SMALL country (e.g. South Korea, Malaysia, Singapore, Netherlands): a city / district -> its
  state/province AND its country (e.g. "Kota Kinabalu" -> "Sabah, Malaysia" and "Malaysia").
- LARGE country (e.g. China, USA, Russia, India, Brazil, Canada, Australia, Indonesia): NEVER
  return the whole country — a candidate on the far side of the country is NOT a match for an
  on-site local role. Widen ONLY to NEARBY major metropolitan areas in the same commuting/
  relocation region, plus at most the state/province (e.g. "Shenzhen" -> "Guangzhou, Guangdong,
  China"; "Dongguan, Guangdong, China"; "Guangdong, China" — NOT "China").
- A location that is ALREADY a whole country CANNOT be widened further -> return NO broader region for it.
- Use names LinkedIn would recognize (countries, states/provinces, large metros).

First identify the country of EACH input location and judge whether it is a LARGE country (too big
for an on-site role to search country-wide — e.g. China/USA/Russia are large; South Korea/Malaysia
are not), then list broader regions (each WITH its country).
Return JSON ONLY:
{{"input_countries": [{{"country": "<country of input location>", "large_country": true/false}}, "..."],
  "broader": [{{"name": "<broader location>", "country": "<its country>"}}, "..."]}}"""


def widen_location_fields(fields, llm, model) -> dict:
    """(v3 / v3.8) Append BROADER geographic tiers to a thin LOCAL market so it yields a larger pool
    — but ONLY within the SAME country/countries the JD listed. Location widening NEVER crosses into a
    different country (e.g. a Korea-only role never pulls India); a country-level location is already
    the broadest allowed and is not widened. For a LARGE country (LLM-judged: China/USA/Russia/…) the
    country itself is NEVER added — widening stops at nearby metros + state/province, so an on-site
    Shenzhen role can pull Guangzhou/Dongguan but not Shanghai/Chengdu. Small countries (Korea,
    Malaysia, …) may widen to the whole country. The country of every broadened facet is checked
    against the input's countries, so an over-broad LLM suggestion is dropped. Mutates + returns
    ``fields``; no-op when there are no locations."""
    existing = [it.get("linkedin") or it.get("raw") for it in (fields.get("location") or [])
                if (it.get("linkedin") or it.get("raw"))]
    if not existing:
        return fields
    try:
        raw, _, _ = llm.invoke(_WIDEN_GEO_PROMPT.format(locs="; ".join(existing)),
                               model_type=model, temperature=0.1)
        data = _extract_json_block(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # allowed countries = those the JD's locations belong to (LLM-identified). If the model didn't
    # identify them, we do NOT widen (safer than risking a cross-country expansion). The LLM also
    # flags each as large_country; large ones never get country-level widening.
    allowed, large = set(), set()
    for c in (data.get("input_countries") or []):
        if isinstance(c, dict):   # {"country": ..., "large_country": ...}
            nm = str(c.get("country") or "").strip().lower()
            if nm:
                allowed.add(nm)
                if c.get("large_country"):
                    large.add(nm)
        elif str(c).strip():      # tolerate the old plain-string form
            allowed.add(str(c).strip().lower())
    seen = {e.lower() for e in existing}
    for b in (data.get("broader") or []):
        if not isinstance(b, dict):
            continue
        name = str(b.get("name") or "").strip()
        ctry = str(b.get("country") or "").strip().lower()
        # GUARDRAIL: keep a broadened location ONLY if its country is one the JD listed.
        if not name or not allowed or ctry not in allowed:
            continue
        # GUARDRAIL: in a LARGE country never widen to the country itself — nearby metros /
        # state/province only (an on-site local role must not pull country-wide candidates).
        if ctry in large and name.lower() == ctry:
            continue
        if name.lower() not in seen:
            fields.setdefault("location", []).append({"raw": name, "linkedin": name, "exists": True})
            seen.add(name.lower())
    return fields


def _name_of(item, raw_key) -> str:
    if item.get("exists") and item.get("linkedin"):
        return item["linkedin"]
    return item.get(raw_key) or item.get("raw") or ""


def _dedup(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


_LANG_SYNONYMS = {
    "mandarin": "Chinese", "mandarin chinese": "Chinese", "putonghua": "Chinese",
    "cantonese": "Chinese", "simplified chinese": "Chinese", "traditional chinese": "Chinese",
    "chinese (mandarin)": "Chinese", "zh": "Chinese", "zh-cn": "Chinese",
}
_LANG_ENUM = None


def normalize_languages(langs, max_search_langs=1):
    """Map synonyms (Mandarin->Chinese), keep only LinkedIn-recruiter-valid languages, then
    CAP the SEARCH to the single most-distinguishing language (prefer non-English)."""
    global _LANG_ENUM
    if _LANG_ENUM is None:
        try:
            try:
                from graphs_v2.config.linkedin_enums import get_linkedin_enum_data
            except ImportError:
                from config.linkedin_enums import get_linkedin_enum_data
            _LANG_ENUM = {x["text"]["text"].lower(): x["text"]["text"]
                          for x in get_linkedin_enum_data()["language_data"]}
        except Exception:
            _LANG_ENUM = {}
    out = []
    for ln in langs or []:
        s = _LANG_SYNONYMS.get(str(ln).strip().lower(), str(ln).strip())
        canon = _LANG_ENUM.get(s.lower()) if _LANG_ENUM else s
        if canon and canon not in out:
            out.append(canon)
    if len(out) > max_search_langs:
        non_en = [l for l in out if l.lower() != "english"]
        out = (non_en or out)[:max_search_langs]
    return out


def _year_widen_deltas(seniorities) -> tuple:
    """(min_delta, max_delta) to widen the search year range, by seniority."""
    names = " ".join(str(s).lower() for s in (seniorities or []))
    is_leadership = any(k in names for k in (
        "director", "vice president", " vp", "cxo", "chief", "head", "owner", "partner",
        "executive", "strategic", "principal"))
    is_junior = (("entry level" in names and "manager" not in names)
                 or any(k in names for k in ("in training", "intern", "junior", "graduate")))
    if is_leadership:
        return 2, 12
    if is_junior:
        return 1, 3
    return 2, 8


_CN_CHAR_MARKERS = ("中国", "北京", "上海", "广州", "深圳", "长沙", "湖南", "重庆", "成都",
                    "杭州", "武汉", "南京", "西安", "天津", "苏州", "省", "市")


def _is_china_location(locations) -> bool:
    """True if the search targets mainland China. LinkedIn canonical names end in
    '..., China' (e.g. 'Beijing, China', 'Changsha, Hunan, China'); also catch raw
    Chinese-character locations that weren't typeahead-resolved. Hong Kong / Taiwan /
    Macau are named separately by LinkedIn and intentionally do NOT match."""
    joined = " ".join(str(l) for l in (locations or []))
    low = joined.lower()
    if "china" in low and not any(x in low for x in ("hong kong", "taiwan", "macau", "macao")):
        return True
    return any(m in joined for m in _CN_CHAR_MARKERS)


def to_optimizer_inputs(fields, keep_industry=True):
    """Build (initial_conditions, mandatory_skills, relaxation_options) for the original
    single_process — mirroring v3.6's bridge. keep_industry=False DROPS the industry
    filter entirely (initial + relaxation) for cross-industry roles."""
    industries = _dedup(_name_of(it, "industry") for it in fields.get("industry", []))
    titles = _dedup(_name_of(it, "title") for it in fields.get("job_title", []))
    locations = _dedup(_name_of(it, "raw") for it in fields.get("location", []))
    job_functions = _dedup(o.get("name") for o in fields.get("job_function", []))
    seniorities = _dedup(o.get("name") for o in fields.get("seniority", []))
    languages = normalize_languages(fields.get("language", []))
    # CHINA QUIRK: Chinese professionals rarely tag Chinese/Mandarin as a LinkedIn profile
    # language, so REQUIRING it collapses the China pool (e.g. Changsha Unity 94 -> 5). For
    # mainland-China searches, drop Chinese-family languages from the search filter; the
    # candidate scorer's language check still handles fit. Non-Chinese langs (e.g. English)
    # are kept.
    if _is_china_location(locations) and languages:
        languages = [l for l in languages if l.lower() not in ("chinese", "mandarin", "cantonese")]
    yoe = fields.get("year_of_experience", {}) or {}

    initial_conditions = {}
    if industries and keep_industry:
        initial_conditions["industry"] = industries[:1]
    if job_functions:
        initial_conditions["job_function"] = job_functions[:1]
    if titles:
        initial_conditions["job_title"] = titles[:2]
    if locations:
        initial_conditions["location"] = {"name": locations}
    if seniorities:
        initial_conditions["seniority"] = seniorities[:1]
    if languages:
        initial_conditions["language"] = languages
    if yoe.get("min_years") is not None or yoe.get("max_years") is not None:
        dmin, dmax = _year_widen_deltas(seniorities)
        mn = max(0, int(yoe.get("min_years") or 0) - dmin)
        mx = min(40, int(yoe.get("max_years") or 30) + dmax)
        initial_conditions["year_of_experience"] = {"start_num_year": mn, "end_num_year": mx}

    mandatory_skills = []
    for it in fields.get("mandatory_skills", []):
        name = _name_of(it, "skill")
        if name:
            # (v3 fix #1/#4) carry the binding flag so the optimizer's downstream enforcement
            # knows which skill(s) to mark required. Base single_process ignores extra keys.
            mandatory_skills.append({"skill": name, "probability": float(it.get("prob") or 0.1),
                                     "binding": bool(it.get("binding"))})

    def _opts(pairs):
        best = {}
        for name, prob in pairs:
            if name and (name not in best or prob > best[name]):
                best[name] = prob
        return [[n, p] for n, p in sorted(best.items(), key=lambda kv: kv[1], reverse=True)]

    relaxation_options = {
        "job_function": _opts((o["name"], float(o.get("prob") or 0.5))
                              for o in fields.get("job_function", []) if o.get("name")),
        "job_title": _opts((_name_of(it, "title"), float(it.get("prob") or 0.1))
                           for it in fields.get("job_title", []) if _name_of(it, "title")),
        "seniority": _opts((o["name"], float(o.get("prob") or 0.5))
                           for o in fields.get("seniority", []) if o.get("name")),
    }
    if keep_industry:
        relaxation_options["industry"] = _opts(
            (_name_of(it, "industry"), float(it.get("prob") or 0.1))
            for it in fields.get("industry", []) if _name_of(it, "industry"))
    return initial_conditions, mandatory_skills, relaxation_options


def _derive_jd_archetypes(job_desc):
    """JD-text-driven archetypes. Returns a list of dicts:
    {label, initial_conditions, mandatory_skills, relaxation_options, allow_remove_industry}
    — one per (baseline + persona), each with its OWN extracted/verified search fields."""
    llm = get_global_llm()
    try:
        personas = generate_archetypes(llm, _V3_FIELD_MODEL, job_desc)[: max(0, MAX_ARCHETYPES - 1)]
    except Exception as e:
        print(f"  [v3-jd] archetype generation failed ({e}); using baseline only.")
        personas = []
    arts = [baseline_archetype()] + personas      # always include the whole-JD baseline

    def _derive_one(a):
        """Extract + verify fields and convert to optimizer inputs for ONE archetype.
        Uses a FRESH ChatGPTWrapper per call: extract_search_fields runs asyncio.run, and a
        shared singleton's AsyncOpenAI client binds to the first thread's event loop, which
        deadlocks the other archetype threads. A per-thread wrapper gives each its own client
        (mirrors v3.6 batch_run, which instantiates ChatGPTWrapper() inside each worker)."""
        wrapper = _ChatGPTWrapper()
        try:
            fields = extract_archetype_fields(job_desc, a, wrapper, _V3_FIELD_MODEL)
            allow_remove = allow_remove_industry_for(a, global_keep_industry=False)
            ic, ms, ro = to_optimizer_inputs(fields, keep_industry=not allow_remove)
        except Exception as e:
            print(f"  [v3-jd] field extraction failed for '{a.get('profile')}': {e}")
            return None
        label = "baseline" if a.get("is_baseline") else f"persona:{a.get('profile')}"
        # (v3) keep the extracted fields so a starved archetype can be re-optimized with
        # geography widened + industry dropped + language stripped (low-yield relaxation).
        return {"label": label, "initial_conditions": ic, "mandatory_skills": ms,
                "relaxation_options": ro, "allow_remove_industry": allow_remove,
                "fields": fields}

    # field extraction is independent per archetype -> run them in parallel.
    workers = max(1, min(len(arts), _ARCHETYPE_WORKERS))
    if workers == 1 or len(arts) <= 1:
        derived = [_derive_one(a) for a in arts]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            derived = list(ex.map(_derive_one, arts))   # map preserves archetype order
    return [d for d in derived if d]


# ---------------------------------------------------------------------------
# 1. Derive archetypes from the parsed conditions (CONDITIONS-ONLY fallback, no JD text)
# ---------------------------------------------------------------------------
def _pair_name(p):
    """relaxation_options entries are [name, prob] (or bare name)."""
    if isinstance(p, (list, tuple)) and p:
        return p[0]
    return p


def _derive_archetypes(initial_conditions, mandatory_skills, relaxation_options):
    """Build up to MAX_ARCHETYPES (label, initial_conditions) variants from the parsed
    conditions. archetype 0 = baseline. Each additional archetype promotes ONE alternative
    job_title (then industry) to PRIMARY. NOTE: the optimizer's relaxation tends to re-merge
    these into one search — prefer the JD-driven path (_derive_jd_archetypes) for real reach."""
    archetypes = [("baseline", copy.deepcopy(initial_conditions))]

    base_titles = list(initial_conditions.get("job_title", []) or [])
    base_inds = list(initial_conditions.get("industry", []) or [])
    primary_title = base_titles[0] if base_titles else None
    primary_ind = base_inds[0] if base_inds else None

    seen = {(primary_title, primary_ind)}

    alt_titles = [_pair_name(p) for p in (relaxation_options.get("job_title", []) or [])]
    for t in alt_titles:
        if len(archetypes) >= MAX_ARCHETYPES:
            break
        if not t or (t, primary_ind) in seen:
            continue
        seen.add((t, primary_ind))
        ic = copy.deepcopy(initial_conditions)
        ic["job_title"] = [t]
        archetypes.append((f"title:{t}", ic))

    alt_inds = [_pair_name(p) for p in (relaxation_options.get("industry", []) or [])]
    for ind in alt_inds:
        if len(archetypes) >= MAX_ARCHETYPES:
            break
        if not ind or (primary_title, ind) in seen:
            continue
        seen.add((primary_title, ind))
        ic = copy.deepcopy(initial_conditions)
        ic["industry"] = [ind]
        archetypes.append((f"industry:{ind}", ic))

    return archetypes


# ---------------------------------------------------------------------------
# 2. Merge archetype results back into one conditions/skills set
# ---------------------------------------------------------------------------
def _merge_conditions(conditions_list):
    """Union the optimized final_conditions of every archetype into one conditions dict."""
    merged = {}
    for c in conditions_list:
        if not isinstance(c, dict):
            continue
        for k in _LIST_KEYS:
            vals = c.get(k)
            if vals:
                bucket = merged.setdefault(k, [])
                for v in vals:
                    if v not in bucket:
                        bucket.append(v)
        loc = c.get("location")
        names = loc.get("name") if isinstance(loc, dict) else None
        if names:
            lb = merged.setdefault("location", {"name": []})["name"]
            for v in names:
                if v not in lb:
                    lb.append(v)
        yoe = c.get("year_of_experience")
        if isinstance(yoe, dict):
            s, e = yoe.get("start_num_year"), yoe.get("end_num_year")
            cur = merged.get("year_of_experience")
            if cur is None:
                merged["year_of_experience"] = {"start_num_year": s, "end_num_year": e}
            else:
                if s is not None:
                    cur["start_num_year"] = s if cur.get("start_num_year") is None else min(cur["start_num_year"], s)
                if e is not None:
                    cur["end_num_year"] = e if cur.get("end_num_year") is None else max(cur["end_num_year"], e)
    return merged


def _merge_skills(skills_lists):
    out = []
    for sl in skills_lists:
        for s in (sl or []):
            name = s.get("skill") if isinstance(s, dict) else s
            if name and name not in out:
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# 2b. Binding-skill enforcement (fixes #1 hard-required skill + #2 relevance-first count)
# ---------------------------------------------------------------------------
def _binding_skill_names(mandatory_skills, top_k):
    """The top-`top_k` DEFINING skill names to enforce as required. Prefer skills flagged
    binding=True (fix #4); otherwise fall back to the highest-probability skill(s)."""
    if top_k <= 0 or not mandatory_skills:
        return []
    flagged = [s for s in mandatory_skills if isinstance(s, dict) and s.get("binding")]
    pool = flagged or sorted(
        (s for s in mandatory_skills if isinstance(s, dict)),
        key=lambda s: float(s.get("probability") or 0), reverse=True)
    names, seen = [], set()
    for s in pool:
        n = str(s.get("skill") or "").strip()
        if n and n.lower() not in seen:
            names.append(n)
            seen.add(n.lower())
        if len(names) >= top_k:
            break
    return names


def _count_ffc(ffc):
    """Recruiter search-count for a ready format_filter_conditions ({'filters': {...}})."""
    try:
        n = _get_rs().get_search_num(ffc).get("num")
        return int(n) if isinstance(n, (int, float)) else None
    except Exception as e:
        print(f"  [v3 binding] search-num failed ({e}).")
        return None


def _binding_keyword_groups(a, require):
    """OR-groups of adjacent terms to enforce for one archetype (fix #2). JD-driven archetypes
    carry fields['binding_keyword_groups'] (LLM-mined defining concepts + English synonyms);
    conditions-only archetypes fall back to the top-`require` mandatory skill name(s) as
    single-term groups. Limited to `require` groups; [] when disabled (require<=0)."""
    if require <= 0:
        return []
    groups = []
    for g in ((a.get("fields") or {}).get("binding_keyword_groups") or []):
        terms = _dedup([str(t).strip() for t in (g or []) if str(t).strip()])
        if terms:
            groups.append(terms)
    if not groups:   # conditions-only fallback: each top skill name is its own group
        groups = [[n] for n in _binding_skill_names(a.get("mandatory_skills"), require)]
    return groups[:require]


def _keyword_expr(groups):
    """Boolean recruiter-keyword expression: OR within each concept group, AND across groups.
    e.g. [["Friction Materials","Brake Pads"],["Korea"]] -> '("Friction Materials" OR "Brake Pads") AND ("Korea")'."""
    parts = []
    for g in groups:
        ors = " OR ".join(f'"{t}"' if " " in t else t for t in g if t)
        if ors:
            parts.append(f"({ors})")
    return " AND ".join(parts)


def _enforce_binding_keywords(ffc, groups):
    """Return (new_ffc, applied_terms): add the binding OR-expression as a hard recruiter
    KEYWORD filter (free text, so friction-ADJACENT profiles still match). ANDs with any
    existing keywords. Keyword text matches the whole profile, not just the skill facet."""
    expr = _keyword_expr(groups)
    if not expr or not isinstance(ffc, dict):
        return ffc, []
    new = copy.deepcopy(ffc)
    filters = new.setdefault("filters", {})
    existing = str(filters.get("keywords") or "").strip()
    filters["keywords"] = f"({existing}) AND {expr}" if existing else expr
    return new, [t for g in groups for t in g]


def _apply_binding_policy(ffc, soft_count, groups, min_relevant=None):
    """Fixes #1+#2. Enforce the defining concept(s) as a hard KEYWORD OR-filter and ACCEPT the
    resulting (smaller) count even below min_target — relevance over volume — provided it clears
    `min_relevant`. If enforcing collapses the pool under that floor (or the count call fails),
    keep the soft result (recall fallback). Returns (ffc, count, enforced: bool, applied_terms)."""
    if min_relevant is None:
        min_relevant = ASO_V3_MIN_RELEVANT
    if not groups:
        return ffc, soft_count, False, []
    enforced_ffc, applied = _enforce_binding_keywords(ffc, groups)
    if not applied:
        return ffc, soft_count, False, []
    cnt = _count_ffc(enforced_ffc)
    if cnt is None:        # keyword filter unsupported/errored -> safe fallback to soft
        return ffc, soft_count, False, []
    if cnt >= min_relevant:
        print(f"  [v3 binding] keywords {_keyword_expr(groups)}: {soft_count} (soft) -> {cnt} (relevant).")
        return enforced_ffc, cnt, True, applied
    print(f"  [v3 binding] keywords {_keyword_expr(groups)} -> {cnt} < floor {min_relevant}; "
          f"keeping soft result ({soft_count}).")
    return ffc, soft_count, False, applied


# ---------------------------------------------------------------------------
# 3. Drop-in single_process (same signature + same return dict)
# ---------------------------------------------------------------------------
def single_process(initial_conditions, mandatory_skills, relaxation_options,
                   min_target=200, max_target=600, tolerant_threshold=25,
                   allow_remove_industry=True, job_desc=None,
                   require_binding_skills=None, min_relevant=None,
                   binding_keyword_groups=None, fields=None,
                   on_archetype_ready=None):
    """Multi-archetype single_process. IDENTICAL output to the original.

    When ``job_desc`` is given, archetypes are derived from the JD TEXT (genuinely distinct
    personas, each with its own extracted/verified fields — the v3.6 approach). Otherwise
    they're derived from the parsed conditions (alternative title/industry promotion).

    require_binding_skills / min_relevant override the module ASO_V3_* defaults per call
    (None -> use the env defaults), so a caller/UI can toggle binding-keyword enforcement (#1).

    binding_keyword_groups: pre-mined OR-groups (from extract_binding_keyword_groups on the JD)
    applied to EVERY archetype — lets a caller enforce binding keywords in fast CONDITIONS-ONLY
    mode (one JD mined once) WITHOUT the slow per-bundle JD-driven persona re-derivation.

    fields: the caller's extracted search fields. In CONDITIONS-ONLY mode these are attached to
    each derived archetype so the LOW-YIELD WIDEN (geo broadened + industry dropped + language
    stripped) can still rescue a thin market — without it, conditions-only starves on niche
    searches (e.g. Korea friction-material) and returns ~0 candidates."""
    # Effective binding-enforcement settings (per-call override of the env defaults).
    _require = ASO_V3_REQUIRE_BINDING_SKILLS if require_binding_skills is None else int(require_binding_skills)
    _min_relevant = ASO_V3_MIN_RELEVANT if min_relevant is None else int(min_relevant)
    # Pre-mined groups (same for all archetypes) take precedence over per-archetype derivation.
    _explicit_groups = [g for g in (binding_keyword_groups or []) if g]
    # Phase-2 sub-timing (derive archetypes / parallel per-archetype opt / merged-union count).
    _pt = {}
    _t_derive = _time.time()
    if job_desc:
        arch_inputs = _derive_jd_archetypes(job_desc)
        if not arch_inputs:   # JD path produced nothing -> fall back to conditions-only
            arch_inputs = [{"label": lbl, "initial_conditions": ic,
                            "mandatory_skills": mandatory_skills,
                            "relaxation_options": relaxation_options,
                            "allow_remove_industry": allow_remove_industry}
                           for lbl, ic in _derive_archetypes(initial_conditions, mandatory_skills, relaxation_options)]
    else:
        # CONDITIONS-ONLY: attach the caller's `fields` to every derived archetype so the
        # low-yield widen (thin-market rescue) can run here too — same as the JD-driven path.
        arch_inputs = [{"label": lbl, "initial_conditions": ic,
                        "mandatory_skills": mandatory_skills,
                        "relaxation_options": relaxation_options,
                        "allow_remove_industry": allow_remove_industry,
                        "fields": fields}
                       for lbl, ic in _derive_archetypes(initial_conditions, mandatory_skills, relaxation_options)]

    # per-archetype upper candidate cap (raised 200 -> 500); never below min_target.
    per_arch_max = max(min_target, MAX_CANDIDATES_PER_ARCHETYPE)
    # (v3) low-yield relaxation threshold: re-optimize a starved archetype with geography
    # widened + industry dropped + language stripped. Default = min_target; 0 disables.
    _low_yield = (int(ASO_V3_LOW_YIELD_RELAX) if ASO_V3_LOW_YIELD_RELAX is not None
                  else int(min_target))

    def _base_opt(ic, ms, ro, allow_remove):
        return _base_single_process(
            copy.deepcopy(ic), copy.deepcopy(ms), copy.deepcopy(ro),
            min_target=min_target, max_target=per_arch_max,
            tolerant_threshold=tolerant_threshold, allow_remove_industry=allow_remove)

    def _row(label, res, t0, relaxed=False, widened=False):
        return {
            "label": label,
            "final_conditions": res.get("final_conditions", {}),
            "final_skills": res.get("final_skills", []),
            "final_count": res.get("final_count", 0),
            "extreme_search_used": res.get("extreme_search_used"),
            "format_filter_conditions": res.get("format_filter_conditions", {}),
            "total_input_tokens": res.get("total_input_tokens", 0),
            "total_output_tokens": res.get("total_output_tokens", 0),
            "opt_sec": round(_time.time() - t0, 2),
            "relaxed": relaxed, "widen_geo": widened,
            "binding_enforced": res.get("binding_enforced") or [],
        }

    def _notify_archetype_ready(row):
        """Invoke the caller's on_archetype_ready callback (if any) with a completed archetype
        row so it can start fetching that condition's candidates IMMEDIATELY, while the other
        archetypes are still optimizing. Called from worker threads; callback errors are
        swallowed so a bad callback can never break the optimization itself."""
        if on_archetype_ready is None:
            return
        try:
            on_archetype_ready(row)
        except Exception as e:
            print(f"  [v3] on_archetype_ready callback failed for '{row.get('label')}': {e}")

    def _optimize_one(a):
        """Run the ORIGINAL single_process for ONE archetype. The optimizer's global search
        cache is keyed by (filters, skills) and only ever appended to (never cleared mid-run),
        so concurrent archetypes safely SHARE it (and skip each other's duplicate lookups).

        Returns a LIST of rows: the BASE archetype, PLUS — when the base is starved
        (optimized count below the low-yield threshold) and a widened re-optimize recovers
        MORE — a SECOND 'widened' archetype entry (geography widened + industry dropped +
        language stripped). Keeping BOTH (rather than replacing) mirrors the v3.8 batch's
        base-fetch + relaxed-refetch UNION, so a caller that fetches each archetype's
        condition collects the same candidate set the batch does."""
        _t0 = _time.time()
        try:
            res = _base_opt(a["initial_conditions"], a["mandatory_skills"], a["relaxation_options"],
                            a.get("allow_remove_industry", allow_remove_industry))
        except Exception as e:
            print(f"  [v3] archetype '{a.get('label')}' optimization failed: {e}")
            return []

        # (v3 fix #1/#2) Enforce the DEFINING concept(s) as a hard KEYWORD OR-filter (adjacent
        # terms, so friction-ADJACENT profiles still match) and accept a smaller, more relevant
        # pool (even below min_target). When this succeeds we have precision, so we SKIP the
        # low-yield widening. Enforcement falls back to the soft result on a thin pool, and is
        # OFF unless require_binding_skills>0 (#1 — the scoring gate handles precision by default).
        groups = (_explicit_groups if (_require > 0 and _explicit_groups)
                  else _binding_keyword_groups(a, _require))
        b_ffc, b_cnt, enforced, applied = _apply_binding_policy(
            res.get("format_filter_conditions", {}), int(res.get("final_count", 0) or 0),
            groups, _min_relevant)
        if enforced:
            res = dict(res)
            res["format_filter_conditions"] = b_ffc
            res["final_count"] = b_cnt
            res["binding_enforced"] = applied
        rows = [_row(a["label"], res, _t0)]
        # PIPELINING HOOK: hand the BASE condition to the caller IMMEDIATELY — before the slow
        # low-yield widen re-optimization below — so a caller can start fetching this archetype's
        # candidates while other archetypes (and this one's widen) are still optimizing.
        _notify_archetype_ready(rows[0])

        if (not enforced and _low_yield > 0 and a.get("fields")
                and a.get("_rank", 0) < ASO_V3_WIDEN_TOP_K
                and int(rows[0]["final_count"] or 0) < _low_yield):
            try:
                fields2 = copy.deepcopy(a["fields"])
                fields2 = widen_location_fields(fields2, get_global_llm(), _V3_FIELD_MODEL)
                fields2["language"] = []                       # strip language for recall
                ic2, ms2, ro2 = to_optimizer_inputs(fields2, keep_industry=False)
                res2 = _base_opt(ic2, ms2, ro2, True)          # industry dropped
                if int(res2.get("final_count", 0) or 0) > int(rows[0]["final_count"] or 0):
                    r2 = _row(a["label"] + " (widened)", res2, _t0, relaxed=True, widened=True)
                    r2["final_count_before"] = int(res.get("final_count", 0) or 0)
                    rows.append(r2)                             # UNION: keep base + widened
                    print(f"  [v3 relax] '{a['label']}' +widened entry: "
                          f"{res.get('final_count')} -> {res2.get('final_count')}")
                    _notify_archetype_ready(r2)
            except Exception as e:
                print(f"  [v3 relax] '{a.get('label')}' relaxation skipped ({e})")
        return rows

    _pt["derive_archetypes_sec"] = round(_time.time() - _t_derive, 2)
    # Tag each archetype with its derivation-priority rank (0 = baseline / most-likely first) so
    # _optimize_one can gate the expensive low-yield widen to only the top-K (ASO_V3_WIDEN_TOP_K).
    for _rank, _a in enumerate(arch_inputs):
        _a["_rank"] = _rank
    # run ALL archetypes' optimizations in parallel (threads — the work is LLM/HTTP-bound).
    workers = max(1, min(len(arch_inputs), _ARCHETYPE_WORKERS))
    _t_opt = _time.time()
    if workers == 1 or len(arch_inputs) <= 1:
        raw_results = [_optimize_one(a) for a in arch_inputs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            raw_results = list(ex.map(_optimize_one, arch_inputs))   # preserves order
    _pt["parallel_opt_sec"] = round(_time.time() - _t_opt, 2)
    _pt["n_archetypes"] = len(arch_inputs)
    _pt["opt_workers"] = workers
    # flatten: each archetype yields 1 row (healthy) or 2 (base + widened when relaxed).
    results = [r for grp in (raw_results or []) for r in (grp or [])]
    _pt["per_archetype"] = [{"label": r.get("label"), "opt_sec": r.get("opt_sec"),
                             "final_count": r.get("final_count"), "widened": r.get("widen_geo")}
                            for r in results]
    in_tok = sum(r.get("total_input_tokens", 0) for r in results)
    out_tok = sum(r.get("total_output_tokens", 0) for r in results)
    if not results:
        raise RuntimeError("v3: all archetype optimizations failed")

    # single archetype (no alternatives derivable) -> behave exactly like the original.
    if len(results) == 1:
        only = results[0]
        base = _rebuild_output(only["final_skills"], only["final_conditions"], only["final_count"],
                               only["format_filter_conditions"], "single_archetype", in_tok, out_tok)
        base["archetypes"] = _archetype_summaries(results)
        _pt["merged_count_sec"] = 0.0
        base["timing_breakdown"] = _pt
        return base

    # ---- merge every archetype's optimized conditions + skills, count the union once ----
    merged_conditions = _merge_conditions([r["final_conditions"] for r in results])
    merged_skills = _merge_skills([r["final_skills"] for r in results])

    final_count = sum(int(r["final_count"] or 0) for r in results)   # fallback (overestimate)
    format_filter_conditions = {}
    _t_merge = _time.time()
    try:
        info = get_linkedin_search_num(merged_conditions, merged_skills, get_global_llm())
        final_count = info.get("search_results_num", final_count)
        format_filter_conditions = info.get("format_filter_conditions", {}) or {}
        in_tok += info.get("input_tokens", 0)
        out_tok += info.get("output_tokens", 0)
    except Exception as e:
        print(f"  [v3] merged-union count failed ({e}); using per-archetype sum {final_count}.")
        biggest = max(results, key=lambda r: int(r["final_count"] or 0))
        format_filter_conditions = biggest["format_filter_conditions"]

    # (v3 fix #1/#2) The merged ffc is rebuilt fresh, so re-apply the keyword enforcement to the
    # condition the caller actually searches. Union every archetype's applied terms into ONE
    # OR-group (a profile matching ANY persona's defining term qualifies). The floor (and the
    # count safety-net) keep it from over-narrowing or breaking if keywords are unsupported.
    merged_terms = _dedup([t for r in results for t in (r.get("binding_enforced") or [])])
    if merged_terms and format_filter_conditions:
        m_ffc, m_cnt, m_enforced, _m_applied = _apply_binding_policy(
            format_filter_conditions, int(final_count or 0), [merged_terms], _min_relevant)
        if m_enforced:
            format_filter_conditions, final_count = m_ffc, m_cnt

    _pt["merged_count_sec"] = round(_time.time() - _t_merge, 2)
    out = _rebuild_output(merged_skills, merged_conditions, final_count,
                          format_filter_conditions, "multi_archetype", in_tok, out_tok)
    out["archetypes"] = _archetype_summaries(results)
    out["timing_breakdown"] = _pt
    _log = " | ".join(f"{r['label']}={r['final_count']}" for r in results)
    print(f"\n[v3 multi-archetype] {len(results)} archetypes (cap {per_arch_max}/each): {_log}")
    print(f"[v3 multi-archetype] merged union final_count = {final_count}")
    return out


def _rebuild_output(final_skills, final_conditions, final_count, format_filter_conditions,
                    extreme_used, in_tok, out_tok):
    """Assemble the EXACT output dict the original single_process returns."""
    _llm = get_global_llm()
    skills_count = len(final_skills or [])
    skills_list_str = ", ".join(f'"{s}"' for s in (final_skills or []))
    path = (f"Multi-archetype optimization: merged {extreme_used}. "
            f"Final result count: {final_count}. Skills ({skills_count}): {skills_list_str}.")
    return {
        "final_skills": final_skills,
        "final_conditions": final_conditions,
        "optimization_path_log": path,
        "rewritten_optim_path": path,
        "final_count": final_count,
        "format_filter_conditions": format_filter_conditions,
        "extreme_search_used": extreme_used,
        "total_input_tokens": in_tok,
        "total_output_tokens": out_tok,
        "llm_provider": "gemini" if "Gemini" in type(_llm).__name__ else "openai",
        "llm_model": "gpt-4.1",
    }


def _archetype_summaries(results):
    """Per-archetype detail. INCLUDES format_filter_conditions so a caller can search
    each archetype's own optimized condition separately (per-archetype download).
    (v3) YIELD-AWARE order: most-productive (highest final_count) first, so a caller using
    only the Top-N archetypes gets the productive ones; relaxed/widen_geo flags are surfaced."""
    ranked = sorted(results, key=lambda r: int(r.get("final_count") or 0), reverse=True)
    return [{"label": r["label"], "final_count": r["final_count"],
             "extreme_search_used": r["extreme_search_used"],
             "final_conditions": r["final_conditions"], "final_skills": r["final_skills"],
             "format_filter_conditions": r.get("format_filter_conditions", {}),
             "opt_sec": r.get("opt_sec"),
             "relaxed": bool(r.get("relaxed")), "widen_geo": bool(r.get("widen_geo")),
             "binding_enforced": r.get("binding_enforced") or [],
             "final_count_before": r.get("final_count_before")}
            for r in ranked]
