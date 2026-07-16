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
    max_search_num=200,                  # per-archetype fetch cap
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

REST service (`serve.py`, FastAPI + uvicorn) on port **5178** by default — override with
`ASO_V3_PORT`:

```bash
docker compose up -d --build          # http://127.0.0.1:5178
ASO_V3_PORT=8080 docker compose up -d --build   # custom 部署端口
curl http://127.0.0.1:5178/v3/health  # verify; Swagger UI at /docs
```

Secrets come from `.env` at runtime (`env_file` in compose.yaml) — they are never baked
into the image (`.env` is in `.dockerignore`).

## Tuning (env vars)

- `ASO_V3_MAX_ARCHETYPES` (5) / `ASO_V3_MAX_PER_ARCHETYPE` (500) / `ASO_V3_ARCHETYPE_WORKERS` (10)
- `ASO_V3_WIDEN_TOP_K` (5) — how many top archetypes are eligible for the low-yield geo widen
- `ASO_V3_REQUIRE_BINDING_SKILLS` (0) / `ASO_V3_MIN_RELEVANT` (5) — binding-skill enforcement
- `ASO_V3_FIELD_MODEL` (gpt-4.1) — LLM for JD-driven field extraction

Geo widening never crosses countries, and for LLM-judged LARGE countries (China/USA/…)
never widens to the country level — nearby metros + state/province only.

Snapshot taken from JDSearchAgent@search_optimization_v3 on 2026-07-12.