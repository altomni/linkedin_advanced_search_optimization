"""API tests via FastAPI TestClient — optimize_and_fetch_union mocked, no live calls."""
import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.v3.endpoints.optimize_and_fetch as oaf_ep
from api.v3.endpoints.health import router as health_router
from api.v3.endpoints.optimize_and_fetch import router as oaf_router


def _client():
    app = FastAPI()
    app.include_router(health_router, prefix="/v3")
    app.include_router(oaf_router, prefix="/v3")
    return TestClient(app)


def test_health():
    r = _client().get("/v3/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["optimizer"] == "advanced_search_optimization_v3"
    assert "max_archetypes" in body and "widen_top_k" in body


def test_optimize_and_fetch_requires_input():
    r = _client().post("/v3/optimize-and-fetch", json={})
    assert r.status_code == 400
    assert "job_desc" in r.json()["detail"]


def test_optimize_and_fetch_validates_types():
    r = _client().post("/v3/optimize-and-fetch", json={"job_desc": "x", "max_search_num": "lots"})
    assert r.status_code == 422


def test_optimize_and_fetch_success_shape(monkeypatch):
    union_df = pd.DataFrame([
        {"linkedin_id": "c1", "first_name": "A", "last_name": "B", "degree": "BSc",
         "location": "Tokyo, Japan", "person_summary": "sum", "cur_job_summary": "cur",
         "prev_job_summary": "prev", "total_experience_years": np.nan,   # NaN must become null
         "education_summary": "edu", "open_to_opportunities": None},
    ])
    stats = {"n_archetypes": 1, "linkedin_count_calls": 7, "union_unique": 1,
             "per_archetype": [{"archetype": "baseline", "fetched": 1}]}
    opt = {"archetypes": [{"label": "baseline", "final_count": 5,
                           "format_filter_conditions": {"filters": {}}}],
           "final_conditions": {"location": {"name": ["Tokyo, Japan"]}},
           "final_count": 5, "final_skills": ["SkillA"]}

    monkeypatch.setattr(oaf_ep, "optimize_and_fetch_union",
                        lambda **kw: (union_df, stats, opt))
    r = _client().post("/v3/optimize-and-fetch", json={"job_desc": "some jd text long enough"})
    assert r.status_code == 200
    body = r.json()
    assert body["union_candidates"][0]["linkedin_id"] == "c1"
    assert body["union_candidates"][0]["total_experience_years"] is None    # NaN -> null
    assert body["stats"]["linkedin_count_calls"] == 7
    assert body["optimization_result"]["final_count"] == 5
    assert body["optimization_result"]["linkedin_count_calls"] == 7
    assert "elapsed_sec" in body


def test_optimize_and_fetch_error_is_500(monkeypatch):
    def boom(**kw):
        raise RuntimeError("optimizer exploded")
    monkeypatch.setattr(oaf_ep, "optimize_and_fetch_union", boom)
    r = _client().post("/v3/optimize-and-fetch", json={"job_desc": "some jd text long enough"})
    assert r.status_code == 500
    assert "optimizer exploded" in r.json()["detail"]
