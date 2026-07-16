import os
import re
import time
from enum import Enum
import asyncio
from typing import Dict, List, Optional

import requests
import aiohttp
from dotenv import load_dotenv

load_dotenv()


class Synonym_Associations(Enum):
    COMPANY = "company"
    LOCATION = "geo"
    SKILL = "skill"
    INDUSTRY = "industry"
    OCCUPATION = "occupation"
    LANGUAGE = "language"
    FIELD_OF_STUDY = "fieldOfStudy"
    DEGREE = "degree"


def parse_enum_category(category):
    if isinstance(category, Synonym_Associations):
        return category  # already an enum member

    if isinstance(category, str):
        try:
            return Synonym_Associations[category.upper()]
        except KeyError:
            for m in Synonym_Associations:
                if m.value.lower() == category.lower():
                    return m

    valid = ", ".join(Synonym_Associations.__members__)  # pretty message
    raise ValueError(f"category must be one of: {valid}")


def get_synonym_associations(category, term_list):
    BASE_URL = os.environ['SYNONYM_ASSOCIATION_API_BASE_URL']
    category_obj = parse_enum_category(category)
    output = {}
    for term in term_list:
        payload = {"type": category_obj.value, "query": term}
        HEADERS = {"Content-Type": "application/json"}
        response = requests.post(f"{BASE_URL}/api/typeahead", json=payload, headers=HEADERS)
        result = response.json()
        print("result json: ", result)
        try:
            search_results = [record['text']['text'] for record in result['result']['result']["elements"]]
            output[term] = search_results
        except:
            continue
    return output


# ── Geo-synonym picker ──────────────────────────────────────────────
# Used after get_synonym_associations_async(LOCATION, …) to choose the
# best canonical name from the typeahead's returned synonym list.
# The LinkedIn typeahead's position-0 result is biased toward exact
# token matches, which means "Paris, France" can land on
# "Paris, Île-de-France, France" or "France" instead of the metro
# name "Greater Paris Metropolitan Region". This picker re-ranks the
# returned synonyms to prefer the metro-area canonical form.

_METRO_KEYWORDS = [
    "greater", "metropolitan area", "metropolitan region",
    "metro region", "metro area", "bay area", "metropolitan",
]
_COMMON_COUNTRIES_LOWER = {
    "united states", "united states of america", "usa", "us", "u s",
    "france", "germany", "uk", "united kingdom", "great britain", "britain",
    "china", "japan", "india", "canada", "australia", "brazil", "mexico",
    "spain", "italy", "netherlands", "sweden", "switzerland", "ireland",
    "belgium", "austria", "poland", "russia", "singapore", "indonesia",
    "thailand", "vietnam", "philippines", "south korea", "korea", "taiwan",
    "new zealand", "south africa", "egypt", "uae", "united arab emirates",
    "saudi arabia", "israel", "turkey", "argentina", "chile", "colombia",
    "peru", "norway", "finland", "denmark", "portugal", "greece", "czechia",
    "czech republic", "hungary", "ukraine", "romania", "bulgaria",
}


def _norm_geo(s: str) -> str:
    """Lower + strip punctuation + collapse whitespace, for comparisons."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def is_metro_synonym(s: str) -> bool:
    """True if the term is a metro-area canonical form (contains a metro
    keyword such as "greater" / "metropolitan area")."""
    s_norm = _norm_geo(s)
    return any(kw in s_norm for kw in _METRO_KEYWORDS)


def city_country_reduction(term: str) -> Optional[str]:
    """Drop embedded sub-national segments (state/province) from a comma-form
    geo term, returning a bare "<City>, <Country>" query — or None if the term
    has fewer than three comma segments (nothing to strip).

    LinkedIn's location typeahead only surfaces the city-level
    "<City>, <Country> Metropolitan Area" entity when queried with the bare
    city+country; including the state (e.g. "Kota Kinabalu, Sabah, Malaysia")
    biases it toward the state-namespaced "Greater ..." / district entities and
    the Metropolitan Area form never appears. Querying this reduction alongside
    the original makes the Metropolitan Area candidate available regardless of
    how the upstream extraction phrased the location.

    Example: "Kota Kinabalu, Sabah, Malaysia" -> "Kota Kinabalu, Malaysia".
    """
    parts = [p.strip() for p in (term or "").split(",") if p.strip()]
    if len(parts) < 3:
        return None
    return f"{parts[0]}, {parts[-1]}"


def _metro_priority(s: str) -> int:
    """Rank a synonym by which metro keyword it matches (lower = preferred).

    The keyword order in _METRO_KEYWORDS is the priority ladder: "greater"
    is LinkedIn's canonical metro prefix, so a "Greater X" form outranks an
    "X Metropolitan Area" form. This makes the choice deterministic when the
    typeahead returns several metro variants for one query (e.g. "Greater
    Kota Kinabalu, Sabah, Malaysia" vs "Kota Kinabalu, Malaysia Metropolitan
    Area") instead of trusting the typeahead's (unstable) result ordering.
    """
    s_norm = _norm_geo(s)
    for idx, kw in enumerate(_METRO_KEYWORDS):
        if kw in s_norm:
            return idx
    return len(_METRO_KEYWORDS)


def _rank_metro_candidates(candidates: List[str], inp_words: set) -> List[str]:
    """Deterministically order metro synonyms: canonical metro form first,
    then the form covering the most input words (more specific / complete,
    e.g. "...Sabah, Malaysia" over "...Area"). Stable for full ties."""
    return sorted(
        candidates,
        key=lambda s: (_metro_priority(s), -len(inp_words & set(_norm_geo(s).split()))),
    )


def pick_canonical_geo_term(input_str: str, synonyms: List[str]) -> str:
    """Pick the canonical *base* LinkedIn entity that best matches the input.

    Unlike pick_best_geo_synonym (which prefers metro forms), this prefers the
    plain city/region entity (e.g. "Kota Kinabalu, Sabah, Malaysia") so it can
    serve as a stable, phrasing-independent anchor for a follow-up metro lookup.
    The LLM extraction phrases the same place differently each run; routing each
    phrasing through this canonical anchor first makes the final expansion
    consistent.

    Selection: among non-metro synonyms (falling back to all synonyms if every
    return is a metro form), choose the one with the most input-word overlap,
    then the most specific (most tokens), with an alphabetical final tiebreak so
    the result is fully deterministic. Bare-country inputs keep country-level
    resolution (delegates to pick_best_geo_synonym).
    """
    if not synonyms:
        return (input_str or "").strip()

    inp_norm = _norm_geo(input_str)
    if inp_norm in _COMMON_COUNTRIES_LOWER:
        # Bare country: don't anchor to a specific city.
        return pick_best_geo_synonym(input_str, synonyms)

    inp_words = set(inp_norm.split())
    non_metro = [s for s in synonyms if not is_metro_synonym(s)]
    pool = non_metro or synonyms
    ranked = sorted(
        pool,
        key=lambda s: (
            -len(inp_words & set(_norm_geo(s).split())),  # most input overlap
            len(_norm_geo(s).split()),                    # simplest (e.g. plain
            #                                               city over "... District")
            _norm_geo(s),                                 # deterministic tiebreak
        ),
    )
    return ranked[0].strip()


def pick_best_geo_synonym(input_str: str, synonyms: List[str]) -> str:
    """Choose the canonical geo name from LinkedIn typeahead synonyms.

    Priority order:
      1. Exact (case- and punctuation-insensitive) match to the input
         → trust the input as canonical.
      2. A synonym containing a metro-area keyword that ALSO shares
         words with the input (e.g., "Greater Paris …" for input
         "Paris France").
      3. Input is not a bare country AND any metro-keyword synonym
         exists (e.g., "Greater Seattle Area" for input "Bellevue WA"
         — picks the metro form returned by LinkedIn even when the
         literal city name isn't in the metro string).
      4. A non-country synonym sharing words with the input (catches
         cases like "Lyon, France" when no metro form exists).
      5. Fallback to the first synonym (preserves prior behavior).

    Returns the chosen synonym as-is (caller may .replace(',', '') to
    match LinkedIn search-API formatting). When the synonym list is
    empty, returns the (stripped) input itself so the caller still has
    a usable location string.
    """
    if not synonyms:
        return (input_str or "").strip()

    inp_norm = _norm_geo(input_str)
    inp_words = set(inp_norm.split())
    inp_looks_metro = any(kw in inp_norm for kw in _METRO_KEYWORDS)

    # 1. Exact match (case/punct insensitive) — only when the input
    #    already looks like a metro form. Otherwise an input like
    #    "Tokyo Japan" would short-circuit to "Tokyo, Japan" even
    #    though "Greater Tokyo Area, Japan" is also returned and is
    #    the preferred canonical form.
    if inp_looks_metro:
        for s in synonyms:
            if _norm_geo(s) == inp_norm:
                return s.strip()

    # Pre-bucket
    metro_overlap, metro_no_overlap = [], []
    for s in synonyms:
        s_norm = _norm_geo(s)
        has_metro_kw = any(kw in s_norm for kw in _METRO_KEYWORDS)
        if has_metro_kw:
            if inp_words & set(s_norm.split()):
                metro_overlap.append(s)
            else:
                metro_no_overlap.append(s)

    # 2. Metro + word overlap with input. Rank the candidates instead of
    #    trusting typeahead order, so a query like "Kota Kinabalu Malaysia"
    #    always resolves to "Greater Kota Kinabalu, Sabah, Malaysia" rather
    #    than flip-flopping with "Kota Kinabalu, Malaysia Metropolitan Area".
    if metro_overlap:
        return _rank_metro_candidates(metro_overlap, inp_words)[0].strip()

    # 3. Any metro-keyword synonym, as long as the input wasn't a bare
    #    country (in which case promoting a specific metro would be
    #    wrong). Typeahead returns metros relevant to the query, so
    #    "Bellevue WA" → "Greater Seattle Area" works even though the
    #    literal city name isn't in the metro string.
    input_is_country = inp_norm in _COMMON_COUNTRIES_LOWER
    if not input_is_country and metro_no_overlap:
        return _rank_metro_candidates(metro_no_overlap, inp_words)[0].strip()

    # 4. Avoid country-only when input had city-level specificity
    if not input_is_country:
        non_country = [s for s in synonyms if _norm_geo(s) not in _COMMON_COUNTRIES_LOWER]
        if non_country:
            with_overlap = [s for s in non_country if set(_norm_geo(s).split()) & inp_words]
            if with_overlap:
                return with_overlap[0].strip()

    # 5. Fallback to the first synonym (matches the legacy behavior)
    return synonyms[0].strip()


async def get_synonym_associations_async(category, term_list, max_concurrent=1, max_retries=3, retry_delay=0.1) -> Dict[str, List[str]]:
    """
    Async parallel version of get_synonym_associations with automatic retry

    Args:
        category: Category enum or string
        term_list: List of terms to search synonyms for
        max_concurrent: Maximum concurrent API calls (default: 1 for conservative approach)
        max_retries: Maximum number of retry attempts per request (default: 3)
        retry_delay: Delay in seconds between retries (default: 0.1)

    Returns:
        Dict mapping term to list of synonym strings
    """
    import time

    BASE_URL = os.environ['SYNONYM_ASSOCIATION_API_BASE_URL']
    category_obj = parse_enum_category(category)

    # Semaphore to control concurrency
    semaphore = asyncio.Semaphore(max_concurrent)

    # Track timing
    total_start = time.time()
    request_times = []

    async def fetch_one(session: aiohttp.ClientSession, term: str, index: int):
        """Fetch synonyms for a single term with concurrency control and retry logic"""
        async with semaphore:
            payload = {"type": category_obj.value, "query": term}
            headers = {"Content-Type": "application/json"}

            # Retry loop
            for attempt in range(max_retries):
                request_start = time.time()

                try:
                    async with session.post(
                        f"{BASE_URL}/api/typeahead",
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        result = await response.json()
                        request_elapsed = time.time() - request_start
                        request_times.append(request_elapsed)

                        retry_info = f" (retry {attempt}/{max_retries-1})" if attempt > 0 else ""
                        print(f"  [{index+1}/{len(term_list)}] Synonym API for '{term}': {request_elapsed:.3f}s{retry_info}")

                        try:
                            search_results = [
                                record['text']['text']
                                for record in result['result']['result']["elements"]
                            ]
                            return term, search_results
                        except (KeyError, TypeError) as e:
                            # Data parsing error - no point retrying
                            print(f"  [{index+1}/{len(term_list)}] Data parsing error for '{term}': {e}")
                            return term, []

                except asyncio.TimeoutError as e:
                    request_elapsed = time.time() - request_start
                    request_times.append(request_elapsed)

                    if attempt < max_retries - 1:
                        print(f"  [{index+1}/{len(term_list)}] Timeout for '{term}' (attempt {attempt+1}/{max_retries}), retrying in {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        print(f"  [{index+1}/{len(term_list)}] Final timeout for '{term}' after {max_retries} attempts ({request_elapsed:.3f}s)")
                        return term, []

                except (aiohttp.ClientError, aiohttp.ServerTimeoutError) as e:
                    request_elapsed = time.time() - request_start
                    request_times.append(request_elapsed)

                    if attempt < max_retries - 1:
                        print(f"  [{index+1}/{len(term_list)}] Network error for '{term}' (attempt {attempt+1}/{max_retries}): {type(e).__name__}, retrying in {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        print(f"  [{index+1}/{len(term_list)}] Final error for '{term}' after {max_retries} attempts ({request_elapsed:.3f}s): {e}")
                        return term, []

                except Exception as e:
                    request_elapsed = time.time() - request_start
                    request_times.append(request_elapsed)

                    if attempt < max_retries - 1:
                        print(f"  [{index+1}/{len(term_list)}] Unexpected error for '{term}' (attempt {attempt+1}/{max_retries}): {e}, retrying in {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        print(f"  [{index+1}/{len(term_list)}] Final unexpected error for '{term}' after {max_retries} attempts ({request_elapsed:.3f}s): {e}")
                        return term, []

            # Should never reach here, but safety fallback
            return term, []

    # Create session and fetch all terms in parallel (controlled by semaphore)
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_one(session, term, i) for i, term in enumerate(term_list)]
        results = await asyncio.gather(*tasks)

    # Convert results to dict
    output = {term: synonyms for term, synonyms in results}

    # Print timing summary
    total_elapsed = time.time() - total_start
    if request_times:
        avg_time = sum(request_times) / len(request_times)
        min_time = min(request_times)
        max_time = max(request_times)
        print(f"  ⏱️  Synonym API Summary: {len(term_list)} requests in {total_elapsed:.3f}s (avg: {avg_time:.3f}s, min: {min_time:.3f}s, max: {max_time:.3f}s, concurrency: {max_concurrent})")

    return output



if __name__ == "__main__":
    # Test skills
    category = Synonym_Associations.SKILL
    skill_name_list = [
        "AI Animation Generation", "AI Agent Evaluation",
        "Friction Material Manufacturing", "Friction", "friction pad" , "Brake Pad", "friction disc"
    ]
    start_time = time.time()
    output = get_synonym_associations(category, skill_name_list)
    print(f"run time: {time.time() - start_time:.2f}s")
    print(output)

    # # Test locations
    # category = Synonym_Associations.LOCATION
    # location_name_list = [
    #     # "Greater Kota Kinabalu Area",
    #     # "Greater Kota Kinabalu Sabah Malaysia",
    #     # "Kota Kinabalu Malaysia Metropolitan Area",
    #     # "Greater Kota Kinabalu, Sabah, Malaysia",
    #     # "West Coast Division, Sabah, Malaysia",
    #     # "Sabah, Malaysia",
    #     'Kota Kinabalu',
    #     # 'kuala Lumpur',
    #     # 'George Town',
    #     # 'Johor Bahru',
    #     # 'Sabah',
    #     # 'Kota Kinabalu',
    #
    # ] #["Boston MA", "Great Boston Area", "US", "Bay Area"]
    # start_time = time.time()
    # output = get_synonym_associations(category, location_name_list)
    # print(f"run time: {time.time() - start_time:.2f}s")
    # print(output)

    # # Test Company
    # category = Synonym_Associations.COMPANY
    # company_name_list = [
    #                     "AWS",
    #                     "Oracle",
    #                     "Microsoft"
    #                      ]  # ["Boston MA", "Great Boston Area", "US", "Bay Area"]
    # start_time = time.time()
    # output = get_synonym_associations(category, company_name_list)
    # print(f"run time: {time.time() - start_time:.2f}s")
    # print(output)

    # # Test Job Title
    # category = Synonym_Associations.
    # company_name_list = [
    #                     "AWS",
    #                     "Oracle",
    #                     "Microsoft"
    #                      ]  # ["Boston MA", "Great Boston Area", "US", "Bay Area"]
    # start_time = time.time()
    # output = get_synonym_associations(category, company_name_list)
    # print(f"run time: {time.time() - start_time:.2f}s")
    # print(output)