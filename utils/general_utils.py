import json
import re
from typing import Any, Tuple, Iterable, List, Dict, Optional

from rapidfuzz import fuzz, process
from docx import Document
from bs4 import BeautifulSoup


def docx_to_text(file_path):
    doc = Document(file_path)
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    return "\n".join(full_text)


def dedup_by_key(lst, key):
    seen = set()
    out = []
    for item in lst:
        k_val = item.get(key)
        if k_val not in seen:
            seen.add(k_val)
            out.append(item)
    return out


def replace_last(text: str, sub: str, repl: str = "") -> str:
    head, sep, tail = text.rpartition(sub)
    return head + repl + tail if sep else text


def extract_json_block(text: str):
    """
    Find the first ```json ... ``` block in `text` and return it as a Python object.
    Falls back to extracting raw JSON ({...} or [...]) if markdown fence is malformed.

    Args:
        text (str): The text containing the JSON block

    Returns:
        dict: Parsed JSON object

    Raises:
        ValueError: If no valid JSON can be extracted
    """
    try:
        # Strategy 1: proper ```json ... ``` fence
        if "```json" in text:
            match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
            if match:
                return json.loads(match.group(1))

            # Strategy 2: ```json without closing ``` (LLM truncation)
            match = re.search(r"```json\s*(.*)", text, re.DOTALL | re.IGNORECASE)
            if match:
                candidate = match.group(1).strip().rstrip("`")
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass  # fall through to Strategy 3

        # Strategy 3: extract first raw JSON object or array from text
        for opener, closer in [("{", "}"), ("[", "]")]:
            start = text.find(opener)
            if start == -1:
                continue
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(text)):
                ch = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break  # malformed, give up on this pair

        # Strategy 4: last resort, try the whole text as JSON
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e.msg}") from e
    except Exception as e:
        raise ValueError(f"Unexpected error: {e}") from e


def json_output_validator(raw_output: str, metadata: dict) -> bool:
    """Validate that LLM output contains parseable JSON.

    Intended as a ``validator`` callback for ``llm.ainvoke`` / ``llm.invoke``.
    Returns ``True`` when *extract_json_block* can parse the output.
    """
    try:
        extract_json_block(raw_output)
        return True
    except ValueError:
        return False


def load_json_string(json_str):
    """
    Safely parse a JSON string with error handling.

    Args:
        json_str (str): The JSON string to parse

    Returns:
        dict or None: Parsed JSON object if successful, None if parsing fails
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {e.msg}")
        print(f"Error at line {e.lineno}, column {e.colno}")
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None


def extract_json_markdown_block(text):
    """
    Extract JSON content from a markdown code block.

    Args:
        text (str): The text containing the markdown JSON block

    Returns:
        str or None: The JSON string if found, None otherwise
    """

    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1)
    else:
        print("No JSON markdown block found.")
        return None


def year_month_diff(start_on: dict, end_on: dict):
    """
    Return the difference between two {year, month} dicts
    as both (total_months, years, months).

    Example
    -------
    >>> start = {'year': 2018, 'month': 10}
    >>> end   = {'year': 2019, 'month': 12}
    >>> year_month_diff(start, end)
    (14, 1, 2)   # 14 months  →  1 year 2 months
    """
    end_on_year = end_on["year"]
    end_on_month = end_on.get("month", 1)
    start_on_year = start_on["year"]
    start_on_month = start_on.get("month", 1)
    total_months = (end_on_year - start_on_year) * 12 + (end_on_month - start_on_month)

    years, months = divmod(total_months, 12)
    return total_months, years, months


def clean_text(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_html_to_text(html: str) -> str:
    """
    Convert HTML to plain text using BeautifulSoup.

    This function removes all HTML tags and entities, preserving only the text content.
    Used to prepare HTML job descriptions for LLM processing.

    Args:
        html: Raw HTML string (e.g., jd_summary, jd_responsibilities, jd_requirements, job_notes)

    Returns:
        Clean plain text with HTML tags removed. Returns empty string if input is None or empty.

    Examples:
        >>> clean_html_to_text("<p>5+ years of <strong>Python</strong> experience</p>")
        '5+ years of Python experience'

        >>> clean_html_to_text("<ul><li>Item 1</li><li>Item 2</li></ul>")
        'Item 1\\nItem 2'

        >>> clean_html_to_text("")
        ''
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Extract text with newline separators for block elements
    # strip=True removes leading/trailing whitespace from each text block
    text = soup.get_text(separator="\n", strip=True)

    return text


def concat_full_name(first: str | None, last: str | None) -> str | None:
    parts = []
    for p in (first, last):
        if isinstance(p, str):
            p = p.strip()
            if p:
                parts.append(p)
    full_name = " ".join(parts)
    if len(full_name.strip()) > 0:
        return full_name
    else:
        return None


def pick_items_by_threshold(pairs: Iterable[Tuple[Any, float]], threshold: float = 0.8) -> tuple[list[Any], float]:
    # Clean and sort by probability (desc)
    cleaned = [(item, float(p)) for item, p in pairs if p is not None]
    cleaned.sort(key=lambda x: x[1], reverse=True)

    selected: List[Any] = []
    cum_prob = 0.0
    for item, prob in cleaned:
        selected.append(item)
        cum_prob += prob
        if cum_prob > threshold:  # strictly greater, as requested
            break
    return selected, cum_prob


def pick_items_from_info_dict_by_threshold(info_list: List[Dict[str, Any]], threshold: float = 0.8, item_key: str = None) -> tuple[list[Any], float]:
    """
    Pick items from a list of dictionaries based on cumulative probability threshold.

    Args:
        info_list: List of dictionaries, each containing an item and "probability" key
                   Example: [{"function": "Engineering", "probability": 0.5}, ...]
        threshold: Cumulative probability threshold (default: 0.8)
        item_key: The key to extract item from each dictionary. If None, extracts first non-probability key

    Returns:
        Tuple of (selected_items, cumulative_probability)
        Example: (['Engineering', 'Senior'], 0.85)
    """
    # Clean and extract (item, probability) pairs
    cleaned = []
    for info_dict in info_list:
        if "probability" not in info_dict or info_dict["probability"] is None:
            continue

        prob = float(info_dict["probability"])

        # Extract the item value
        if item_key is not None:
            if item_key in info_dict:
                item = info_dict[item_key]
            else:
                # Skip if specified key doesn't exist
                continue
        else:
            # Extract the value from the first non-probability key
            # Get all keys except 'probability'
            non_prob_keys = [k for k in info_dict.keys() if k != "probability"]
            if non_prob_keys:
                # Use the first non-probability key's value
                item = info_dict[non_prob_keys[0]]
            else:
                # Skip if no other keys exist
                continue

        cleaned.append((item, prob))

    # Sort by probability (desc)
    cleaned.sort(key=lambda x: x[1], reverse=True)

    selected: List[Any] = []
    cum_prob = 0.0
    for item, prob in cleaned:
        selected.append(item)
        cum_prob += prob
        if cum_prob > threshold:  # strictly greater, as requested
            break
    return selected, cum_prob


def fuzzy_match_function_name(query: str, function_name_2_id: Dict[str, str], threshold: float = 80.0) -> Optional[Tuple[str, str]]:
    """
    Use fuzzy matching to find the best matching function name from function_name_2_id.

    Args:
        query: The function name to match
        function_name_2_id: Dictionary mapping function names to IDs
        threshold: Minimum similarity score (0-100) to consider a match

    Returns:
        Tuple of (matched_name, function_id) if found, None otherwise
    """
    if not function_name_2_id:
        return None

    # Get list of function names for fuzzy matching
    function_names = list(function_name_2_id.keys())

    # Use rapidfuzz to find the best match
    # process.extractOne returns (match, score, index) or None
    best_match = process.extractOne(
        query,
        function_names,
        scorer=fuzz.WRatio,  # Weighted ratio for better partial matching
        score_cutoff=threshold
    )

    if best_match:
        matched_name = best_match[0]
        return matched_name, function_name_2_id[matched_name]

    return None


def unpack_llm_result(raw: Any) -> Tuple[Dict, int, int]:
    """
    Safely unpack an LLM extraction result into (info_dict, input_tokens, output_tokens).

    Handles two return conventions:
    - tuple/list of (dict, input_tokens, output_tokens)  — e.g. extract_apn_salary, extract_language
    - dict with optional "tokenUsage" key               — e.g. extract_apn_job_function, extract_apn_degree

    Never raises; returns ({}, 0, 0) on unexpected input.
    """
    if isinstance(raw, (tuple, list)) and len(raw) == 3:
        info, in_tok, out_tok = raw
        return (info if isinstance(info, dict) else {}), (in_tok or 0), (out_tok or 0)
    if isinstance(raw, dict):
        usage = raw.get("tokenUsage") or {}
        return raw, usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    return {}, 0, 0