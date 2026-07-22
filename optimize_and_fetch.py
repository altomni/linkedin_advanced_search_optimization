"""Optimize → fetch → union, in one file.

Runs advanced_search_optimization_v3.single_process (multi-archetype optimization), then for
EACH optimized archetype condition fetches candidates with batch_basic_linkedin_search (the
search response already contains full profiles — no per-ID download), extracts candidate info,
and UNIONS everything deduplicated by linkedin_id. Output: the union candidate DataFrame,
ready for downstream evaluation.

Library use:
    from optimize_and_fetch import optimize_and_fetch_union
    union_df, stats = optimize_and_fetch_union(job_desc=open("jd.txt").read())

CLI:
    python optimize_and_fetch.py path/to/jd.txt [--max-search 500] [--out union.csv]

(Synced from JDSearchAgent/src/graphs_v2/optimize_and_fetch.py — PIPELINED version: each
archetype's fetch starts the moment its optimization finishes.)
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Project root on sys.path so top-level imports (advanced_search_optimization_v3, utils) resolve.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

import pandas as pd

from advanced_search_optimization_v3 import (
    single_process,
    clear_optimization_caches,
    reset_linkedin_call_count,
    get_linkedin_call_count,
)
from utils.search_utils import batch_basic_linkedin_search, extract_candidate_info

# Column order matches extract_candidate_info(include_education_summary=True); the names match
# what the dynamic evaluator expects when building candidate_summary.
CANDIDATE_COLUMNS = [
    "linkedin_id", "first_name", "last_name", "degree", "location", "person_summary",
    "cur_job_summary", "prev_job_summary", "total_experience_years",
    "education_summary", "open_to_opportunities",
]


def _fetch_archetype(archetype, final_skills, max_search_num, channel):
    """Fetch ONE archetype condition's candidates. Returns (label, [candidate tuples])."""
    label = archetype.get("label")
    conditions = archetype.get("format_filter_conditions") or {}
    est_count = archetype.get("final_count") or 100
    try:
        raw_results = batch_basic_linkedin_search(
            est_count, conditions, final_skills,
            max_search_num=max_search_num, channel=channel)
    except Exception as e:
        print(f"  [fetch] archetype '{label}' search failed: {e}")
        return label, []
    rows = []
    for job_info in raw_results:
        info = extract_candidate_info(job_info, channel=channel, include_education_summary=True)
        if info is None:
            continue
        info = tuple(info) + (None,) * (len(CANDIDATE_COLUMNS) - len(info))  # pad short tuples
        rows.append(info[:len(CANDIDATE_COLUMNS)])
    print(f"  [fetch] {label}: {len(raw_results)} raw -> {len(rows)} extracted")
    return label, rows


def optimize_and_fetch_union(job_desc=None, initial_conditions=None, mandatory_skills=None,
                             relaxation_options=None, min_target=200, max_target=600,
                             max_search_num=500, channel="recruiter", workers=10,
                             clear_caches=True, return_optimization=False,
                             on_fetch_done=None):
    """Steps 1-3 of the graphs_v2 pipeline's search phase, self-contained:
    1. v3 multi-archetype optimization -> several optimized search conditions;
    2. per-condition candidate fetch (profiles ride along with the search results);
    3. union deduplicated by linkedin_id.

    Returns (union_df, stats) — or (union_df, stats, optimization_result) when
    return_optimization=True (callers that display the archetype conditions, e.g. the
    standalone streamlit app). NOTE: this is the primary per-archetype fetch only — the
    graphs_v2 search_graph's company/language second searches and the education-based
    language filter are not part of this file.

    on_fetch_done(archetype_row, label, rows): optional callback fired from a fetch-pool
    thread as soon as EACH archetype's fetch completes (rows = tuples in CANDIDATE_COLUMNS
    order). Lets callers stream partial results (e.g. the async worker pushing candidates
    to the reranking Redis queue) instead of waiting for the full union."""
    if clear_caches:
        clear_optimization_caches()
    reset_linkedin_call_count()

    # PIPELINED fetch: as EACH archetype finishes optimizing, single_process hands its row to
    # on_archetype_ready and the fetch for that condition starts IMMEDIATELY on the fetch pool —
    # overlapping with the remaining archetypes' (and widen re-optimizations') LLM/count work,
    # instead of waiting for the whole optimization to finish.
    fetch_pool = ThreadPoolExecutor(max_workers=max(1, workers))
    fetch_futures = []          # (archetype_row, future) in readiness order
    _submitted = set()
    _submit_lock = threading.Lock()

    def _submit_fetch(a):
        if not ((a.get("format_filter_conditions") or {}).get("filters")):
            return
        with _submit_lock:
            if a.get("label") in _submitted:
                return
            _submitted.add(a.get("label"))
            print(f"  [pipeline] '{a.get('label')}' optimized -> fetch started", flush=True)
            fut = fetch_pool.submit(_fetch_archetype, a, a.get("final_skills") or [],
                                    max_search_num, channel)
            if on_fetch_done is not None:
                def _notify(f, _a=a):
                    try:
                        label, rows = f.result()
                        on_fetch_done(_a, label, rows)
                    except Exception as e:
                        print(f"  [pipeline] on_fetch_done failed for '{_a.get('label')}': {e}")
                fut.add_done_callback(_notify)
            fetch_futures.append((a, fut))

    t0 = time.time()
    optimization_result = single_process(
        initial_conditions=initial_conditions or {},
        mandatory_skills=mandatory_skills or [],
        relaxation_options=relaxation_options or {},
        min_target=min_target, max_target=max_target,
        job_desc=job_desc,
        on_archetype_ready=_submit_fetch,
    )
    optimize_seconds = round(time.time() - t0, 1)
    final_skills = optimization_result.get("final_skills") or []
    archetypes = [a for a in (optimization_result.get("archetypes") or [])
                  if ((a.get("format_filter_conditions") or {}).get("filters"))]
    # Safety net: submit anything the callback did not deliver (older single_process without
    # the hook, or rows filtered/renamed along the way). Normally a no-op.
    for a in archetypes:
        _submit_fetch(a)
    print(f"[optimize] {len(archetypes)} archetype condition(s) in {optimize_seconds}s "
          f"({get_linkedin_call_count()} count-probe calls); "
          f"{sum(1 for _, f in fetch_futures if f.done())} fetch(es) already done (pipelined)")

    t1 = time.time()
    per_archetype = []
    frames = []
    for a, fut in fetch_futures:
        label, rows = fut.result()
        per_archetype.append({"archetype": label, "fetched": len(rows),
                              "relaxed": bool(a.get("relaxed")),
                              "widen_geo": bool(a.get("widen_geo"))})
        if rows:
            frames.append(pd.DataFrame(rows, columns=CANDIDATE_COLUMNS))
    fetch_pool.shutdown(wait=True)

    if frames:
        union_df = (pd.concat(frames, ignore_index=True)
                    .drop_duplicates(subset=["linkedin_id"]).reset_index(drop=True))
    else:
        union_df = pd.DataFrame(columns=CANDIDATE_COLUMNS)

    stats = {
        "n_archetypes": len(archetypes),
        "final_count_estimate": optimization_result.get("final_count"),
        "final_skills": final_skills,
        "linkedin_count_calls": get_linkedin_call_count(),
        "per_archetype": per_archetype,
        "total_fetched": sum(p["fetched"] for p in per_archetype),
        "union_unique": len(union_df),
        "optimize_seconds": optimize_seconds,
        # With pipelining, fetches overlap optimization; this is only the RESIDUAL wait for
        # fetches still in flight after single_process returned (~0 when fully overlapped).
        "fetch_seconds": round(time.time() - t1, 1),
    }
    print(f"[union] {stats['total_fetched']} fetched across {len(archetypes)} archetype(s) "
          f"-> {stats['union_unique']} unique candidates in {stats['fetch_seconds']}s")
    if return_optimization:
        return union_df, stats, optimization_result
    return union_df, stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="v3 optimize + per-archetype fetch + union")
    ap.add_argument("jd_file", help="path to a JD text file")
    ap.add_argument("--min-target", type=int, default=200)
    ap.add_argument("--max-target", type=int, default=600)
    ap.add_argument("--max-search", type=int, default=500, help="per-archetype fetch cap")
    ap.add_argument("--channel", default="recruiter", choices=["recruiter", "sales_nav"])
    ap.add_argument("--out", default="union_candidates.csv", help="output CSV path")
    args = ap.parse_args()

    union_df, stats = optimize_and_fetch_union(
        job_desc=open(args.jd_file).read(),
        min_target=args.min_target, max_target=args.max_target,
        max_search_num=args.max_search, channel=args.channel)

    union_df.to_csv(args.out, index=False)
    print(f"\nunion df saved: {args.out} ({len(union_df)} candidates)")
    print(json.dumps(stats, ensure_ascii=False, indent=2))