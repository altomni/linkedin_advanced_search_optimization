# linkedin_advanced_search_optimization

Self-contained deployment of `advanced_search_optimization_v3.py` (the multi-archetype
LinkedIn search-condition optimizer) extracted from the JDSearchAgent repo, with its full
transitive dependency closure copied in. No import from the parent repo is required.

## Layout

```
optimize_and_fetch.py                # entry point: optimize_and_fetch_union(...) = optimize -> fetch -> union
advanced_search_optimization_v3.py   # the optimizer: single_process(...), used by optimize_and_fetch.py
prompts.py                           # LLM extraction prompt templates
jd_smart_interactive_search_process.py / jd_understanding_funcs.py / linkedin_integration_service.py
config/                              # config, api_config, linkedin_enums + linkedin_enum_data/*.json
llms/                                # ChatGPTWrapper (gpt-4.1), deepseek, qwen wrappers
utils/                               # recruiter_api_formatter, search_utils, synonym_association, ...
linkedin_apiservice/                 # low-level LinkedIn API client stack
linkedin_recruiter_apiservice/       # RecruiterService (count probes + search)
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in the keys, contact for the setting details
```

## Usage

```python
from optimize_and_fetch import optimize_and_fetch_union

union_df, stats = optimize_and_fetch_union(
    job_desc=open("my_jd.txt").read(),   # JD-driven multi-archetype mode
    min_target=200, max_target=600,
    max_search_num=500,                  # per-archetype fetch cap
)
# union_df -> candidate DataFrame, unioned across archetypes, deduped by linkedin_id
# stats    -> n_archetypes, per_archetype fetch counts, final_count_estimate,
#             union_unique, optimize_seconds / fetch_seconds
```

Or from the command line:

```bash
python example.py path/to/jd.txt
# or with more knobs:
python optimize_and_fetch.py path/to/jd.txt --max-search 200 --out union.csv
```

## Deployment (Docker)

The compose stack runs three services: the REST API (`serve.py`, FastAPI + uvicorn, port
**5178** — override with `ASO_V3_PORT`), the async task worker (`optimize_fetch_worker.py`,
consumes `/v3/optimize-fetch-tasks` jobs), and a bundled `redis` (queue/status/result
store — used unless `.env` sets `REDIS_HOST` to an external Redis). See
`DEPLOYMENT_ASYNC_OPTFETCH.md` for the async task pattern details.

**Deploy / start serving:**

```bash
docker compose up -d --build          # build + start all services -> http://127.0.0.1:5178
ASO_V3_PORT=8080 docker compose up -d --build   # custom 部署端口
curl http://127.0.0.1:5178/v3/health  # verify; Swagger UI at /docs

docker compose up -d --scale optfetch-worker=3  # more concurrent async tasks
```

**Stop serving:**

```bash
docker compose stop                   # stop all services; containers + Redis data kept
docker compose start                  # resume serving (no rebuild)
```

Call the deployed service with one JD over HTTP (start the service first) — using the
sample JD in `examples/`:

```bash
python examples/example_service.py examples/senior_backend_engineer_jd.txt
# non-default host/port:
ASO_V3_SERVICE_URL=http://127.0.0.1:8080 python examples/example_service.py examples/senior_backend_engineer_jd.txt
```

This POSTs to `/v3/optimize-and-fetch` and writes the unioned candidates to
`union_candidates.csv` — the HTTP counterpart of the in-process `example.py`.

---

## Async task flow (curl): submit → poll status → download candidates

The synchronous `/v3/optimize-and-fetch` call blocks for several minutes. The async task
endpoints return immediately and run the job in the worker, with Redis as the
queue/status/result store:

```bash
BASE_URL=http://localhost:5178   # replace with your deployed host

# 0. (optional) confirm the service is up
curl -s $BASE_URL/v3/health

# 1. Submit the job — returns 202 + task_id immediately
TASK_ID=$(curl -s -X POST $BASE_URL/v3/optimize-fetch-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "job_desc": "Senior Backend Engineer with 5+ years Python, Kubernetes... (full JD text)",
    "min_target": 200,
    "max_target": 600,
    "max_search_num": 500,
    "channel": "recruiter"
  }' | jq -r '.task_id')
echo "task_id: $TASK_ID"

# 2. Check status: queued -> running -> done | failed
#    (include_result=false keeps the response small while it runs;
#    archetypes_done / candidates_pushed advance per archetype)
curl -s "$BASE_URL/v3/optimize-fetch-tasks/$TASK_ID?include_result=false" | jq

#    ...or poll automatically until it finishes:
while true; do
  STATUS=$(curl -s "$BASE_URL/v3/optimize-fetch-tasks/$TASK_ID?include_result=false" | jq -r '.status')
  echo "$(date +%T) status=$STATUS"
  [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] && break
  sleep 15
done

# 3. Download the result once done (stored in Redis at optfetch:result:{task_id})
curl -s "$BASE_URL/v3/optimize-fetch-tasks/$TASK_ID" | jq '.result' > result.json                          # full result: candidates + stats + archetype conditions
curl -s "$BASE_URL/v3/optimize-fetch-tasks/$TASK_ID" | jq -r '.result.union_candidates_csv' > candidates.csv  # just the candidate table
```

`candidates.csv` columns: `linkedin_id, first_name, last_name, degree, location,
person_summary, cur_job_summary, prev_job_summary, total_experience_years,
education_summary, open_to_opportunities`.

Notes:

- `job_desc` and/or `initial_conditions` is required; optional fields include `workers`
  (fetch pool size) and `push_to_rerank` + `rerank_job_context` to stream each
  archetype's candidates into the reranking Redis queue as they are fetched.
- The result has a TTL in Redis — download it reasonably soon after completion; after
  expiry the GET returns 404 ("Unknown or expired task").
- To bypass the API and read Redis directly (e.g. debugging inside the compose network):
  `redis-cli GET optfetch:result:<task_id>` (result JSON) and
  `redis-cli HGETALL optfetch:task:<task_id>` (status hash).

Secrets come from `.env` at runtime (`env_file` in compose.yaml) — they are never baked
into the image (`.env` is in `.dockerignore`).

Tear the stack down completely when you're done (vs. `stop`, which keeps everything for
a quick `start`):

```bash
docker compose down                   # stop + remove containers and network (Redis volume kept)
docker compose down -v                # also remove the Redis data volume (queued tasks/results lost)
docker compose down --rmi local       # also remove the built linkedin-aso image
docker compose logs -f linkedin-aso   # (optional) tail API logs; use optfetch-worker for the worker
```

---

## Testing

Offline suite (all LLM / LinkedIn calls are mocked — no network, no API cost):

```bash
pytest tests/
```

Live integration test against a **running Docker-deployed service**
(`tests/test_docker_service.py`) — self-skips when the service is down, so it never
breaks the offline run:

```bash
docker compose up -d --build                 # start the service first

pytest tests/test_docker_service.py          # health + input-validation only (fast, free)
ASO_V3_RUN_FETCH=1 pytest tests/test_docker_service.py   # + full /v3/optimize-and-fetch
```

- Target a non-default host/port with `ASO_V3_SERVICE_URL` (default `http://127.0.0.1:5178`).
- The whole module skips unless `/v3/health` is reachable.
- `test_optimize_and_fetch_live` additionally requires `ASO_V3_RUN_FETCH=1`, because it
  runs real LLM extraction + LinkedIn fetches (minutes of wall-clock, real API cost).

## Tuning (env vars)

- `ASO_V3_MAX_ARCHETYPES` (5) / `ASO_V3_MAX_PER_ARCHETYPE` (500) / `ASO_V3_ARCHETYPE_WORKERS` (10)
- `ASO_V3_WIDEN_TOP_K` (5) — how many top archetypes are eligible for the low-yield geo widen
- `ASO_V3_REQUIRE_BINDING_SKILLS` (0) / `ASO_V3_MIN_RELEVANT` (5) — binding-skill enforcement
- `ASO_V3_FIELD_MODEL` (gpt-4.1) — LLM for JD-driven field extraction

Geo widening never crosses countries, and for LLM-judged LARGE countries (China/USA/…)
never widens to the country level — nearby metros + state/province only.

Snapshot taken from JDSearchAgent@search_optimization_v3 on 2026-07-12.