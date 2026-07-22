"""Shared Redis key schema + helpers for the async optimize-and-fetch task pattern.

Used by BOTH sides of the async split:
  - api/v3/endpoints/optimize_fetch_async.py  (enqueue + status polling)
  - optimize_fetch_worker.py                  (execution)

Key schema (all namespaced under "optfetch:"):
  optfetch:queue               input queue (Redis list). API RPUSHes task messages
                               {"task_id": ..., "params": ...}; worker polls with LPOP.
  optfetch:task:{task_id}      task hash: status, timestamps, progress counters, error.
  optfetch:result:{task_id}    final result JSON (union candidates CSV + stats), TTL-bound.

Task lifecycle: queued -> running -> done | failed
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone

import redis

# Same env vars as JDSearchAgent's reranking/optfetch services so all share one Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

QUEUE_KEY = os.getenv("OPTFETCH_QUEUE", "optfetch:queue")
TASK_KEY_PREFIX = "optfetch:task:"
RESULT_KEY_PREFIX = "optfetch:result:"

# Results kept 24h after completion; a task record that is never picked up expires in 7d
RESULT_TTL_SECONDS = int(os.getenv("OPTFETCH_RESULT_TTL_SECONDS", str(24 * 3600)))
QUEUED_TTL_SECONDS = int(os.getenv("OPTFETCH_QUEUED_TTL_SECONDS", str(7 * 24 * 3600)))

# Where streamed per-archetype candidates go (consumed by JDSearchAgent's reranking_redis_io)
RERANK_INPUT_QUEUE = os.getenv("RERANK_INPUT_QUEUE", "reranking:input")

# Crash recovery: workers heartbeat on the task hash while running; a "running" task whose
# heartbeat is older than STALE_SECONDS is presumed orphaned (worker died / was redeployed)
# and gets requeued up to MAX_RETRIES times, then marked failed.
HEARTBEAT_SECONDS = int(os.getenv("OPTFETCH_HEARTBEAT_SECONDS", "15"))
STALE_SECONDS = int(os.getenv("OPTFETCH_STALE_SECONDS", "120"))
MAX_RETRIES = int(os.getenv("OPTFETCH_MAX_RETRIES", "1"))
RECLAIM_LOCK_KEY = "optfetch:reclaim_lock"


def get_redis_client() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_key(task_id: str) -> str:
    return f"{TASK_KEY_PREFIX}{task_id}"


def result_key(task_id: str) -> str:
    return f"{RESULT_KEY_PREFIX}{task_id}"


def enqueue_task(r: redis.Redis, params: dict) -> str:
    """Create the task record and push the message onto the input queue. Returns task_id."""
    task_id = uuid.uuid4().hex
    key = task_key(task_id)
    message = json.dumps({"task_id": task_id, "params": params}, ensure_ascii=False, default=str)
    pipe = r.pipeline()
    pipe.hset(key, mapping={
        "status": "queued",
        "created_at": now_iso(),
        "archetypes_done": 0,
        "candidates_pushed": 0,
        "retries": 0,
        # Keep the full queue message on the hash so a stale task can be requeued verbatim
        "message": message,
    })
    pipe.expire(key, QUEUED_TTL_SECONDS)
    pipe.rpush(QUEUE_KEY, message)
    pipe.execute()
    return task_id


def get_task_status(r: redis.Redis, task_id: str) -> dict | None:
    """Task hash as a dict of strings, or None if unknown/expired."""
    h = r.hgetall(task_key(task_id))
    return h or None


def get_task_result(r: redis.Redis, task_id: str) -> dict | None:
    raw = r.get(result_key(task_id))
    return json.loads(raw) if raw else None


def queue_depth(r: redis.Redis) -> int:
    return r.llen(QUEUE_KEY)


def reclaim_stale_tasks(r: redis.Redis) -> int:
    """Requeue "running" tasks whose worker stopped heartbeating (crashed, OOM-killed,
    redeployed mid-task). Returns how many were requeued. Safe to call from every worker
    on a timer: a short NX lock ensures only one reclaimer sweeps at a time.

    A task past MAX_RETRIES (or predating the heartbeat schema) is marked failed instead.
    """
    if not r.set(RECLAIM_LOCK_KEY, "1", nx=True, ex=60):
        return 0
    requeued = 0
    try:
        cutoff = time.time() - STALE_SECONDS
        for key in r.scan_iter(f"{TASK_KEY_PREFIX}*", count=100):
            h = r.hgetall(key)
            if h.get("status") != "running":
                continue
            heartbeat = float(h.get("heartbeat_at") or 0)
            if heartbeat >= cutoff:
                continue
            retries = int(h.get("retries") or 0)
            message = h.get("message")
            if message and retries < MAX_RETRIES:
                pipe = r.pipeline()
                pipe.hset(key, mapping={
                    "status": "queued",
                    "retries": retries + 1,
                    "requeued_at": now_iso(),
                })
                pipe.hdel(key, "heartbeat_at")
                pipe.rpush(QUEUE_KEY, message)
                pipe.execute()
                requeued += 1
                print(f"[optfetch] requeued stale task {key} (retry {retries + 1}/{MAX_RETRIES})")
            else:
                r.hset(key, mapping={
                    "status": "failed",
                    "finished_at": now_iso(),
                    "error": f"worker died mid-task (no heartbeat for >{STALE_SECONDS}s); "
                             f"retries exhausted ({retries}/{MAX_RETRIES})",
                })
                print(f"[optfetch] marked stale task {key} failed (retries exhausted)")
    finally:
        r.delete(RECLAIM_LOCK_KEY)
    return requeued