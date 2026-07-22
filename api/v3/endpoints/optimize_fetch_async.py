"""Async optimize-and-fetch task endpoints (async task pattern with Redis).

The optimize+fetch takes several minutes — too long for a synchronous HTTP call. These
endpoints implement the async task pattern; Redis is the queue + status/result store:

  POST /v3/optimize-fetch-tasks            -> 202 + task_id immediately (just enqueues)
  GET  /v3/optimize-fetch-tasks/{task_id}  -> status/progress; result when done

Nothing long-running happens in the API process. Execution belongs to
optimize_fetch_worker.py, a separate process that polls the queue
(run it alongside the API: python optimize_fetch_worker.py).
"""
import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import redis as redis_lib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from optimize_fetch_redis import (
    get_redis_client,
    enqueue_task,
    get_task_status,
    get_task_result,
    queue_depth,
)

router = APIRouter()


class OptimizeFetchTaskRequest(BaseModel):
    """Request to ENQUEUE an optimize-and-fetch task. POST returns 202 + task_id
    immediately; the job runs in optimize_fetch_worker.py. Poll the status endpoint."""
    job_desc: Optional[str] = Field(None, description="JD text — enables JD-driven multi-archetype mode")
    initial_conditions: Dict[str, Any] = Field(default_factory=dict)
    mandatory_skills: List[Any] = Field(default_factory=list)
    relaxation_options: Dict[str, Any] = Field(default_factory=dict)
    min_target: int = Field(200, ge=0)
    max_target: int = Field(600, ge=0)
    max_search_num: int = Field(500, ge=1, le=500, description="per-archetype fetch cap")
    channel: str = Field("recruiter")
    workers: int = Field(10, ge=1, le=32, description="fetch thread-pool size inside the worker")
    job_id: Optional[str] = Field("", description="Job ID for tracking")
    push_to_rerank: bool = Field(
        False,
        description="Stream each archetype's candidates into the reranking Redis input queue "
                    "as soon as they are fetched (deduplicated by linkedin_id)"
    )
    rerank_job_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Job context merged into each streamed candidate record "
                    "(job_desc, job_summary, job_location, ...)"
    )


class OptimizeFetchTaskCreatedResponse(BaseModel):
    """202 response - task accepted, not yet run"""
    task_id: str
    status: str = "queued"
    status_url: str
    queue_depth: int = Field(0, description="Tasks waiting ahead in the queue at enqueue time")
    message: Optional[str] = None


class OptimizeFetchTaskStatusResponse(BaseModel):
    """Lifecycle: queued -> running -> done | failed. While running, archetypes_done and
    candidates_pushed advance per archetype. When done (and include_result=true), result
    contains union_candidates_csv + stats."""
    task_id: str
    status: str = Field(..., description="queued | running | done | failed")
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    archetypes_done: int = 0
    candidates_pushed: int = 0
    retries: int = Field(0, description="Times the task was requeued after a worker died mid-run")
    candidates_count: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None


def _redis_or_503() -> redis_lib.Redis:
    r = get_redis_client()
    try:
        r.ping()
    except redis_lib.RedisError as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")
    return r


@router.post("/optimize-fetch-tasks", status_code=202, response_model=OptimizeFetchTaskCreatedResponse)
def create_optimize_fetch_task(request: OptimizeFetchTaskRequest):
    """Enqueue an optimize-and-fetch task and return immediately (202)."""
    if not request.job_desc and not request.initial_conditions:
        raise HTTPException(status_code=422, detail="Provide job_desc and/or initial_conditions")

    r = _redis_or_503()
    depth = queue_depth(r)
    task_id = enqueue_task(r, request.model_dump())
    return OptimizeFetchTaskCreatedResponse(
        task_id=task_id,
        status="queued",
        status_url=f"/v3/optimize-fetch-tasks/{task_id}",
        queue_depth=depth,
        message=f"Task queued ({depth} ahead). Poll status_url for progress; "
                f"typical run time is several minutes.",
    )


@router.get("/optimize-fetch-tasks/{task_id}", response_model=OptimizeFetchTaskStatusResponse)
def get_optimize_fetch_task(task_id: str, include_result: bool = True):
    """Poll a task: queued | running | done | failed. When done, the result
    (union_candidates_csv + stats) is included unless include_result=false."""
    r = _redis_or_503()
    status = get_task_status(r, task_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Unknown or expired task: {task_id}")

    task_state = status.get("status", "queued")
    result = None
    if include_result and task_state == "done":
        result = get_task_result(r, task_id)

    return OptimizeFetchTaskStatusResponse(
        task_id=task_id,
        status=task_state,
        created_at=status.get("created_at"),
        started_at=status.get("started_at"),
        finished_at=status.get("finished_at"),
        archetypes_done=int(status.get("archetypes_done") or 0),
        candidates_pushed=int(status.get("candidates_pushed") or 0),
        retries=int(status.get("retries") or 0),
        candidates_count=int(status["candidates_count"]) if status.get("candidates_count") else None,
        result=result,
        error=status.get("error"),
        message=f"Task is {task_state}",
    )