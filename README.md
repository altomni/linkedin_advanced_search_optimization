# linkedin_advanced_search_optimization

Self-contained deployment of `advanced_search_optimization_v3.py` (the multi-archetype
LinkedIn search-condition optimizer) extracted from the JDSearchAgent repo, with its full
transitive dependency closure copied in. No import from the parent repo is required.

## Layout

```
advanced_search_optimization_v3.py   # the optimizer: single_process(...) is the entry point
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
from advanced_search_optimization_v3 import single_process

result = single_process(
    initial_conditions={}, mandatory_skills=[], relaxation_options={},
    min_target=200, max_target=600,
    job_desc=open("my_jd.txt").read(),   # JD-driven multi-archetype mode
)
# result["archetypes"]      -> one optimized search condition per archetype (+ widened variants)
# result["final_count"]     -> merged union estimated count
# result["format_filter_conditions"] -> merged recruiter-format condition
```

Or `python example.py path/to/jd.txt`.

## Tuning (env vars)

- `ASO_V3_MAX_ARCHETYPES` (5) / `ASO_V3_MAX_PER_ARCHETYPE` (500) / `ASO_V3_ARCHETYPE_WORKERS` (10)
- `ASO_V3_WIDEN_TOP_K` (5) — how many top archetypes are eligible for the low-yield geo widen
- `ASO_V3_REQUIRE_BINDING_SKILLS` (0) / `ASO_V3_MIN_RELEVANT` (5) — binding-skill enforcement
- `ASO_V3_FIELD_MODEL` (gpt-4.1) — LLM for JD-driven field extraction

Geo widening never crosses countries, and for LLM-judged LARGE countries (China/USA/…)
never widens to the country level — nearby metros + state/province only.

Snapshot taken from JDSearchAgent@search_optimization_v3 on 2026-07-12.