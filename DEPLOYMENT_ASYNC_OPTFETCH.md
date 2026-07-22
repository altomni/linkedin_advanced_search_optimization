# Deploying the async optimize-and-fetch stack

`optimize_and_fetch.py` takes several minutes per JD, so it also runs behind an async
task API (in addition to the synchronous /v3/optimize-and-fetch):

```
client ──POST /v3/optimize-fetch-tasks──► API ──RPUSH──► Redis optfetch:queue
client ◄──202 {task_id}──────────────────┘
                                                │ LPOP (2s poll)
                                                ▼
                                       optimize_fetch_worker.py ──► optfetch:task:{id}   (status/progress)
                                                │                   optfetch:result:{id} (union CSV + stats, TTL 24h)
                                                └─(push_to_rerank)─► reranking:input     (per-archetype streaming)
client ──GET /v3/optimize-fetch-tasks/{task_id}──► API ──reads task/result keys──► Redis
```

## Run modes

**docker compose:**

```bash
docker compose up -d --build      # redis + API (:5178) + 1 worker
docker compose logs -f optfetch-worker
docker compose up -d --scale optfetch-worker=3   # more concurrent tasks
```

Redis connection settings come from `.env` (`REDIS_HOST/PORT/DB`) and apply to BOTH the
API and the worker — they must agree, or tasks get enqueued where no worker looks.
Without `REDIS_HOST` in `.env`, the stack defaults to the bundled `redis` service.
After changing `.env`, recreate the services (`docker compose up -d`) — a plain restart
keeps old env values.

**Local dev:** `python optimize_fetch_worker.py` in one terminal (needs a reachable
Redis), `python serve.py` in another.

## Configuration (env vars, all optional)

| Var | Default | Meaning |
|---|---|---|
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD` | `localhost` / `6379` / `0` / none | Shared Redis |
| `OPTFETCH_QUEUE` | `optfetch:queue` | Input queue key |
| `OPTFETCH_RESULT_TTL_SECONDS` | `86400` (24h) | Result retention after completion |
| `OPTFETCH_QUEUED_TTL_SECONDS` | `604800` (7d) | Retention for tasks never picked up |
| `OPTFETCH_HEARTBEAT_SECONDS` | `15` | Worker heartbeat interval while running |
| `OPTFETCH_STALE_SECONDS` | `120` | No heartbeat for this long ⇒ task presumed orphaned |
| `OPTFETCH_MAX_RETRIES` | `1` | Requeues before an orphaned task is marked failed |
| `RERANK_INPUT_QUEUE` | `reranking:input` | Where `push_to_rerank` tasks stream candidates |

## Failure recovery

- Task state lives in Redis (`optfetch:task:{id}` hash), not in any process. Lifecycle:
  `queued → running → done | failed`.
- While running, the worker heartbeats every 15s. Every worker also sweeps once a minute
  (`reclaim_stale_tasks`, guarded by an NX lock) for `running` tasks with a stale
  heartbeat — a worker that crashed or was redeployed mid-task — and requeues them
  (up to `OPTFETCH_MAX_RETRIES`, then `failed`). The status API exposes `retries`.
- SIGTERM: the worker finishes cleanly if idle; if killed mid-task after the grace
  period, the reclaim sweep recovers the task.
- Caveat: a retried `push_to_rerank` task may re-stream candidates the first attempt
  already pushed (dedup is per-attempt). Downstream consumers should treat
  `linkedin_id` upserts as idempotent.

## Smoke test

```bash
curl -sX POST localhost:5178/v3/optimize-fetch-tasks -H 'Content-Type: application/json' \
  -d "{\"job_desc\": $(python -c 'import json;print(json.dumps(open("examples/<some_jd>.txt").read()))')}"
# → {"task_id": "...", "status_url": "/v3/optimize-fetch-tasks/..."}
curl -s "localhost:5178/v3/optimize-fetch-tasks/<task_id>?include_result=false"   # poll

# pull the candidate CSV once status=done
curl -s "localhost:5178/v3/optimize-fetch-tasks/<task_id>" | \
  python3 -c "import sys, json; sys.stdout.write(json.load(sys.stdin)['result']['union_candidates_csv'])" > candidates.csv
```