"""Live integration tests against a running Docker-deployed service.

Unlike every other test in this suite (which mocks all LLM / LinkedIn calls), these hit a
REAL running instance of serve.py — normally the one from `docker compose up -d --build`.

They are OPT-IN and self-skip so a plain offline `pytest` run stays green:

  * The service URL comes from ASO_V3_SERVICE_URL (default http://127.0.0.1:5178).
  * Every test skips if /v3/health is not reachable, so nothing fails when the
    container is down.
  * test_optimize_and_fetch_live additionally requires ASO_V3_RUN_FETCH=1 because it
    performs real LLM extraction + LinkedIn fetches (minutes of wall-clock, real API cost).

Usage:
    docker compose up -d --build
    pytest tests/test_docker_service.py                 # health check only
    ASO_V3_RUN_FETCH=1 pytest tests/test_docker_service.py   # + full optimize-and-fetch
"""
import os

import pytest
import requests

BASE_URL = os.getenv("ASO_V3_SERVICE_URL", "http://127.0.0.1:5178").rstrip("/")


def _service_up():
    try:
        r = requests.get(f"{BASE_URL}/v3/health", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


# Skip the whole module unless a deployed service answers /v3/health.
pytestmark = pytest.mark.skipif(
    not _service_up(),
    reason=f"no deployed service reachable at {BASE_URL} "
           f"(start it with `docker compose up -d --build`)",
)


def test_health_live():
    r = requests.get(f"{BASE_URL}/v3/health", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["optimizer"] == "advanced_search_optimization_v3"
    assert "max_archetypes" in body and "widen_top_k" in body


def test_optimize_and_fetch_requires_input_live():
    # No job_desc and no initial_conditions -> validated 400 from the live endpoint.
    r = requests.post(f"{BASE_URL}/v3/optimize-and-fetch", json={}, timeout=10)
    assert r.status_code == 400
    assert "job_desc" in r.json()["detail"]


_SAMPLE_JD = """\
Senior Backend Software Engineer

We are hiring a Senior Backend Software Engineer for our platform team in San Francisco, CA.
Requirements:
- 5+ years of backend software engineering experience
- Strong Python; Go a plus
- Distributed systems, microservices, and REST API design
- AWS, Docker, Kubernetes, PostgreSQL, Redis
"""


@pytest.mark.skipif(
    os.getenv("ASO_V3_RUN_FETCH") != "1",
    reason="slow live test: real LLM extraction + LinkedIn fetch (minutes, API cost). "
           "Set ASO_V3_RUN_FETCH=1 to enable.",
)
def test_optimize_and_fetch_live():
    payload = {
        "job_desc": _SAMPLE_JD,
        "min_target": 200,
        "max_target": 600,
        "max_search_num": 200,
    }
    r = requests.post(f"{BASE_URL}/v3/optimize-and-fetch", json=payload, timeout=900)
    assert r.status_code == 200, r.text
    body = r.json()

    # Top-level shape.
    for key in ("union_candidates", "stats", "optimization_result", "elapsed_sec"):
        assert key in body, f"missing top-level key: {key}"

    stats = body["stats"]
    assert stats["n_archetypes"] >= 1
    assert stats["union_unique"] == len(body["union_candidates"])

    # If any candidates came back, they carry the expected record fields.
    if body["union_candidates"]:
        assert "linkedin_id" in body["union_candidates"][0]

    assert isinstance(body["optimization_result"].get("archetypes"), list)
