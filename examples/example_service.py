"""Minimal example: call the Docker-deployed service with one JD.

Unlike example.py (which imports optimize_and_fetch_union and runs in-process), this hits the
running REST service over HTTP — start it first with `docker compose up -d --build`.

Usage (run from the repo root):
    python examples/example_service.py examples/senior_backend_engineer_jd.txt
    ASO_V3_SERVICE_URL=http://127.0.0.1:8080 python examples/example_service.py examples/senior_backend_engineer_jd.txt

The service URL comes from ASO_V3_SERVICE_URL (default http://127.0.0.1:5178).
"""
import os
import sys

import pandas as pd
import requests

BASE_URL = os.getenv("ASO_V3_SERVICE_URL", "http://127.0.0.1:5178").rstrip("/")

jd_path = sys.argv[1] if len(sys.argv) > 1 else None
if not jd_path:
    sys.exit("usage: python example_service.py path/to/jd.txt")

payload = {
    "job_desc": open(jd_path).read(),
    "min_target": 200, "max_target": 600,
    "max_search_num": 200,   # per-archetype fetch cap
}

# Fail fast with a clear message if the service isn't up.
try:
    health = requests.get(f"{BASE_URL}/v3/health", timeout=5)
    health.raise_for_status()
except requests.RequestException as e:
    sys.exit(f"service not reachable at {BASE_URL} ({e})\n"
             f"start it with: docker compose up -d --build")

print(f"POST {BASE_URL}/v3/optimize-and-fetch  (real LLM + LinkedIn fetch, may take minutes)")
resp = requests.post(f"{BASE_URL}/v3/optimize-and-fetch", json=payload, timeout=900)
if resp.status_code != 200:
    sys.exit(f"request failed: HTTP {resp.status_code}\n{resp.text}")

body = resp.json()
stats = body["stats"]

print(f"\narchetypes: {stats['n_archetypes']}")
for p in stats["per_archetype"]:
    print(f"  - {p['archetype']}: fetched {p['fetched']}"
          f"{' (widened)' if p.get('widen_geo') else ''}")
print(f"merged union estimated count: {stats.get('final_count_estimate')}")
print(f"unique candidates fetched: {stats['union_unique']}")
print(f"service elapsed: {body.get('elapsed_sec')}s")

union_df = pd.DataFrame(body["union_candidates"])
union_df.to_csv("union_candidates.csv", index=False)
print(f"\nunion df saved: union_candidates.csv ({len(union_df)} candidates)")
