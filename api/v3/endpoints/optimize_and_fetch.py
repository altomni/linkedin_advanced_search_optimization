"""v3 optimize-and-fetch endpoint: v3 multi-archetype optimization + PIPELINED per-condition
candidate fetch + union (optimize_and_fetch.optimize_and_fetch_union), returned as JSON.

POST /optimize-and-fetch
  in : {job_desc?, initial_conditions?, mandatory_skills?, relaxation_options?,
        min_target?, max_target?, max_search_num?}
  out: {union_candidates: [..], stats: {..}, optimization_result: {archetypes, final_conditions,
        final_count, linkedin_count_calls, ...}}
"""
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from optimize_and_fetch import optimize_and_fetch_union

router = APIRouter()
# Module-level optimizer caches are shared; one optimization at a time per worker process.
_run_lock = threading.Lock()


class OptimizeAndFetchRequest(BaseModel):
    job_desc: Optional[str] = Field(None, description="JD text — enables JD-driven multi-archetype mode")
    initial_conditions: Dict[str, Any] = Field(default_factory=dict)
    mandatory_skills: List[Any] = Field(default_factory=list)
    relaxation_options: Dict[str, Any] = Field(default_factory=dict)
    min_target: int = Field(200, ge=0)
    max_target: int = Field(600, ge=0)
    max_search_num: int = Field(200, ge=1, le=500, description="per-archetype fetch cap")
    channel: str = Field("recruiter")


def _json_safe(x):
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    return str(x)


@router.post("/optimize-and-fetch")
def optimize_and_fetch(req: OptimizeAndFetchRequest):
    if not req.job_desc and not req.initial_conditions:
        raise HTTPException(status_code=400, detail="Provide job_desc and/or initial_conditions.")
    t0 = time.time()
    with _run_lock:
        try:
            union_df, stats, optimization_result = optimize_and_fetch_union(
                job_desc=req.job_desc,
                initial_conditions=req.initial_conditions,
                mandatory_skills=req.mandatory_skills,
                relaxation_options=req.relaxation_options,
                min_target=req.min_target, max_target=req.max_target,
                max_search_num=req.max_search_num, channel=req.channel,
                return_optimization=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    # NaN -> None so the payload is strict JSON.
    union_records = union_df.astype(object).where(union_df.notna(), None).to_dict(orient="records")
    return {
        "union_candidates": _json_safe(union_records),
        "stats": _json_safe(stats),
        "optimization_result": _json_safe({
            "archetypes": optimization_result.get("archetypes"),
            "final_conditions": optimization_result.get("final_conditions"),
            "final_count": optimization_result.get("final_count"),
            "final_skills": optimization_result.get("final_skills"),
            "linkedin_count_calls": stats.get("linkedin_count_calls"),
        }),
        "elapsed_sec": round(time.time() - t0, 1),
    }
