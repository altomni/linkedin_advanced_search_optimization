"""Optimize-and-fetch worker - executes async tasks enqueued by /v3/optimize-fetch-tasks.

Flow:
1. Poll the optfetch:queue Redis list (LPOP every 2s) for task messages
2. Mark the task hash "running", then run optimize_and_fetch.optimize_and_fetch_union
3. As EACH archetype's fetch completes (on_fetch_done), bump progress counters and — if the
   task asked for it — stream that archetype's candidates straight into the reranking input
   queue (deduplicated by linkedin_id) so downstream evaluation starts minutes before the
   whole multi-minute job finishes
4. Write the final result (union CSV + stats) to optfetch:result:{task_id} with a TTL and
   mark the task "done" (or "failed" with the error)

Run:
    python optimize_fetch_worker.py

One task at a time per process — the job itself is heavily parallel inside (LLM calls +
a 10-thread fetch pool), and advanced_search_optimization_v3 keeps process-global caches/
counters, so intra-process task concurrency is unsafe. To scale throughput, run more
worker processes; LPOP is atomic, so each queued task goes to exactly one worker.

NOTE: deliberately LPOP+sleep, NOT BLPOP — redis-py 8.0.x raises TimeoutError when a
blocking command's timeout expires instead of returning None, which crash-loops the worker.

Deployment behavior:
- While a task runs, a daemon thread heartbeats on the task hash every HEARTBEAT_SECONDS.
- Every worker periodically sweeps for "running" tasks with a stale heartbeat (worker
  crashed / was redeployed mid-task) and requeues them — see reclaim_stale_tasks().
- SIGTERM/SIGINT: the worker exits at the next idle moment. If it is killed mid-task
  (e.g. compose stop_grace_period elapses), the reclaim sweep recovers the task.
"""
import json
import os
import signal
import sys
import threading
import time
import traceback

import redis

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

from optimize_and_fetch import optimize_and_fetch_union, CANDIDATE_COLUMNS
from optimize_fetch_redis import (
    get_redis_client,
    now_iso,
    task_key,
    result_key,
    reclaim_stale_tasks,
    QUEUE_KEY,
    RESULT_TTL_SECONDS,
    QUEUED_TTL_SECONDS,
    RERANK_INPUT_QUEUE,
    HEARTBEAT_SECONDS,
)

POLL_INTERVAL_SECONDS = 2
ERROR_BACKOFF_SECONDS = 5
RECLAIM_INTERVAL_SECONDS = 60

_stop_requested = False


def _handle_stop_signal(signum, frame):
    global _stop_requested
    _stop_requested = True
    print(f"[worker] received signal {signum}; exiting at next idle moment "
          "(a mid-task kill is recovered by the stale-task reclaim sweep)")


def process_task(r, msg: dict):
    task_id = msg["task_id"]
    params = msg.get("params") or {}
    tkey = task_key(task_id)

    r.hset(tkey, mapping={"status": "running", "started_at": now_iso(),
                          "heartbeat_at": time.time()})
    r.expire(tkey, QUEUED_TTL_SECONDS)
    print(f"[worker] task {task_id} started (job_id={params.get('job_id', '')!r})")

    # Heartbeat so other workers' reclaim sweeps know this task is alive
    heartbeat_stop = threading.Event()

    def _heartbeat_loop():
        while not heartbeat_stop.wait(HEARTBEAT_SECONDS):
            try:
                r.hset(tkey, "heartbeat_at", time.time())
            except Exception as e:
                print(f"[worker] heartbeat failed for task {task_id}: {e}")

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    push_to_rerank = bool(params.get("push_to_rerank"))
    job_context = params.get("rerank_job_context") or {}
    seen_ids = set()
    seen_lock = threading.Lock()

    def on_fetch_done(archetype, label, rows):
        """Runs on a fetch-pool thread each time one archetype's candidates arrive."""
        payloads = []
        if push_to_rerank and rows:
            with seen_lock:
                for row in rows:
                    rec = dict(zip(CANDIDATE_COLUMNS, row))
                    linkedin_id = rec.get("linkedin_id")
                    if not linkedin_id or linkedin_id in seen_ids:
                        continue
                    seen_ids.add(linkedin_id)
                    rec.update(job_context)
                    rec["task_id"] = task_id
                    payloads.append(json.dumps(rec, ensure_ascii=False, default=str))
        pipe = r.pipeline()
        pipe.hincrby(tkey, "archetypes_done", 1)
        if payloads:
            pipe.rpush(RERANK_INPUT_QUEUE, *payloads)
            pipe.hincrby(tkey, "candidates_pushed", len(payloads))
        pipe.execute()
        note = f", {len(payloads)} streamed to {RERANK_INPUT_QUEUE}" if payloads else ""
        print(f"[worker] task {task_id}: archetype '{label}' -> {len(rows)} candidates{note}")

    try:
        union_df, stats, optimization_result = optimize_and_fetch_union(
            job_desc=params.get("job_desc"),
            initial_conditions=params.get("initial_conditions"),
            mandatory_skills=params.get("mandatory_skills"),
            relaxation_options=params.get("relaxation_options"),
            min_target=params.get("min_target", 200),
            max_target=params.get("max_target", 600),
            max_search_num=params.get("max_search_num", 500),
            channel=params.get("channel", "recruiter"),
            workers=params.get("workers", 10),
            return_optimization=True,
            on_fetch_done=on_fetch_done,
        )
        result = {
            "task_id": task_id,
            "job_id": params.get("job_id", ""),
            "candidates_count": int(len(union_df)),
            "union_candidates_csv": union_df.to_csv(index=False),
            "stats": stats,
            # Archetype conditions / final_count etc. for UIs that render the optimization
            # (e.g. JDSearchAgent's whole_pipeline_v3_standalone_streamlit). JSON-safe via
            # the default=str dump below.
            "optimization_result": optimization_result,
        }
        pipe = r.pipeline()
        pipe.set(result_key(task_id),
                 json.dumps(result, ensure_ascii=False, default=str),
                 ex=RESULT_TTL_SECONDS)
        pipe.hset(tkey, mapping={
            "status": "done",
            "finished_at": now_iso(),
            "candidates_count": int(len(union_df)),
        })
        pipe.expire(tkey, RESULT_TTL_SECONDS)
        pipe.execute()
        print(f"[worker] task {task_id} done: {len(union_df)} unique candidates")
    except Exception as e:
        traceback.print_exc()
        pipe = r.pipeline()
        pipe.hset(tkey, mapping={
            "status": "failed",
            "error": str(e)[:2000],
            "finished_at": now_iso(),
        })
        pipe.expire(tkey, RESULT_TTL_SECONDS)
        pipe.execute()
        print(f"[worker] task {task_id} FAILED: {e}")
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)


def run_worker():
    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    r = get_redis_client()
    try:
        r.ping()
    except Exception as e:
        print(f"[ERROR] Cannot connect to Redis: {e}")
        sys.exit(1)
    print(f"[worker] connected, polling queue '{QUEUE_KEY}' every {POLL_INTERVAL_SECONDS}s")

    last_reclaim = 0.0
    while not _stop_requested:
        if time.monotonic() - last_reclaim >= RECLAIM_INTERVAL_SECONDS:
            try:
                reclaim_stale_tasks(r)
            except Exception as e:
                print(f"[WARN] stale-task reclaim failed: {e}")
            last_reclaim = time.monotonic()

        try:
            raw = r.lpop(QUEUE_KEY)
        except redis.RedisError as e:
            print(f"[WARN] Redis poll failed ({type(e).__name__}: {e}); retrying in {ERROR_BACKOFF_SECONDS}s")
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue
        if raw is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[WARN] skipping malformed task message: {e}")
            continue
        if not msg.get("task_id"):
            print(f"[WARN] skipping task message without task_id")
            continue
        process_task(r, msg)

    print("[worker] stopped")


if __name__ == "__main__":
    run_worker()