"""Unit tests for the offline helpers in advanced_search_optimization_v3."""
import json
import threading

import advanced_search_optimization_v3 as aso

from conftest import FakeLLM


# ---------------------------------------------------------------------------
# LinkedIn count-probe call counter
# ---------------------------------------------------------------------------
def test_call_counter_reset_and_increment():
    aso.reset_linkedin_call_count()
    assert aso.get_linkedin_call_count() == 0
    aso._count_linkedin_call()
    aso._count_linkedin_call()
    assert aso.get_linkedin_call_count() == 2
    aso.reset_linkedin_call_count()
    assert aso.get_linkedin_call_count() == 0


def test_call_counter_thread_safety():
    aso.reset_linkedin_call_count()
    threads = [threading.Thread(target=lambda: [aso._count_linkedin_call() for _ in range(100)])
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert aso.get_linkedin_call_count() == 800
    aso.reset_linkedin_call_count()


# ---------------------------------------------------------------------------
# Optimization caches
# ---------------------------------------------------------------------------
def test_clear_caches_and_stats():
    aso._search_results_cache["k"] = {"x": 1}
    aso._conditions_cache["k"] = {"y": 2}
    aso.clear_optimization_caches()
    stats = aso.get_cache_stats()
    assert stats["search_results_cache_size"] == 0
    assert stats["conditions_cache_size"] == 0


# ---------------------------------------------------------------------------
# Language normalization (uses config/linkedin_enum_data on disk, no network)
# ---------------------------------------------------------------------------
def test_normalize_languages_maps_synonyms_and_caps():
    out = aso.normalize_languages(["Mandarin", "English"])
    assert out == ["Chinese"]            # synonym mapped, capped to 1, non-English preferred


def test_normalize_languages_empty():
    assert aso.normalize_languages([]) == []
    assert aso.normalize_languages(None) == []


# ---------------------------------------------------------------------------
# Geo widening guardrails (LLM faked — no API)
# ---------------------------------------------------------------------------
def _widen(fields, llm_json):
    llm = FakeLLM([json.dumps(llm_json)])
    return aso.widen_location_fields(fields, llm, "fake-model"), llm


def test_widen_large_country_never_adds_country():
    fields = {"location": [{"raw": "Shenzhen, Guangdong, China",
                            "linkedin": "Shenzhen, Guangdong, China", "exists": True}]}
    out, _ = _widen(fields, {
        "input_countries": [{"country": "China", "large_country": True}],
        "broader": [{"name": "Guangzhou, Guangdong, China", "country": "China"},
                    {"name": "Guangdong, China", "country": "China"},
                    {"name": "China", "country": "China"}],       # must be dropped
    })
    names = [x["linkedin"] for x in out["location"]]
    assert "Guangzhou, Guangdong, China" in names
    assert "Guangdong, China" in names
    assert "China" not in names


def test_widen_small_country_allows_country():
    fields = {"location": [{"raw": "Kota Kinabalu, Sabah, Malaysia",
                            "linkedin": "Kota Kinabalu, Sabah, Malaysia", "exists": True}]}
    out, _ = _widen(fields, {
        "input_countries": [{"country": "Malaysia", "large_country": False}],
        "broader": [{"name": "Sabah, Malaysia", "country": "Malaysia"},
                    {"name": "Malaysia", "country": "Malaysia"}],
    })
    names = [x["linkedin"] for x in out["location"]]
    assert "Malaysia" in names


def test_widen_never_crosses_country():
    fields = {"location": [{"raw": "Seoul, South Korea",
                            "linkedin": "Seoul, South Korea", "exists": True}]}
    out, _ = _widen(fields, {
        "input_countries": [{"country": "South Korea", "large_country": False}],
        "broader": [{"name": "Tokyo, Japan", "country": "Japan"},        # cross-country: dropped
                    {"name": "South Korea", "country": "South Korea"}],
    })
    names = [x["linkedin"] for x in out["location"]]
    assert "Tokyo, Japan" not in names
    assert "South Korea" in names


def test_widen_no_locations_is_noop():
    out, llm = _widen({"location": []}, {})
    assert out["location"] == []
    assert llm.prompts == []             # LLM never called


def test_widen_llm_garbage_is_safe():
    fields = {"location": [{"raw": "Tokyo, Japan", "linkedin": "Tokyo, Japan", "exists": True}]}
    llm = FakeLLM(["not json at all"])
    out = aso.widen_location_fields(fields, llm, "fake-model")
    assert [x["linkedin"] for x in out["location"]] == ["Tokyo, Japan"]   # unchanged
