"""
queue.py — Redis priority queue with leases (at-least-once, idempotent results).

Redis keys (all prefixed with `ns`, default ""):
  job:<id>         JSON of the job (incl. its queue score)
  jobs             ZSET  id -> score        lowest score is popped first
  processing       HASH  id -> lease expiry (unix seconds)
  owner            HASH  id -> worker_id    (debugging only)
  result:<id>      JSON result; written once (SET NX) -> completion is idempotent
  retries:<id>     INT   how many times a lease on this job expired
  counters         HASH  completed / failed / retries_total
  recent           LIST  last 1000 completed ids, newest first (stats API)
  worker_done      HASH  worker_id -> jobs completed
  workers          HASH  worker_id -> last heartbeat (unix seconds)

Why Lua: a Redis script runs atomically, so "take a job AND take its lease" can
never be interleaved with another client (no lost job, no double pop), and two
reapers can never both requeue the same expired lease.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import redis

from backend.cloud.jobs import Job
from backend.cloud.scoring import compute_score

# pop: take the best-scored job and lease it, in one atomic step.
_POP_LUA = """
-- KEYS: jobs, processing, owner      ARGV: lease_seconds, worker_id, ns
while true do
  local popped = redis.call('ZPOPMIN', KEYS[1], 1)        -- {id, score} or {}
  if #popped == 0 then return false end                   -- queue empty
  local id = popped[1]
  -- skip ids that already have a result (a requeued duplicate of a finished job)
  if redis.call('EXISTS', ARGV[3] .. 'result:' .. id) == 0 then
    local t = redis.call('TIME')
    local now = t[1] + t[2] / 1000000
    redis.call('HSET', KEYS[2], id, now + tonumber(ARGV[1]))   -- lease deadline
    redis.call('HSET', KEYS[3], id, ARGV[2])
    return id
  end
end
"""

# complete: first writer wins. NX makes a duplicate completion a no-op.
_COMPLETE_LUA = """
-- KEYS: result, processing, owner, jobs, counters, recent, worker_done
-- ARGV: result_json, id, counter_field, worker_id
local first = redis.call('SET', KEYS[1], ARGV[1], 'NX')
redis.call('HDEL', KEYS[2], ARGV[2])      -- release the lease either way
redis.call('HDEL', KEYS[3], ARGV[2])
redis.call('ZREM', KEYS[4], ARGV[2])      -- drop any requeued duplicate still waiting
if first then
  redis.call('HINCRBY', KEYS[5], ARGV[3], 1)
  redis.call('LPUSH', KEYS[6], ARGV[2])   -- `recent`: newest finished ids, for the stats API
  redis.call('LTRIM', KEYS[6], 0, 999)
  if ARGV[4] ~= '' then redis.call('HINCRBY', KEYS[7], ARGV[4], 1) end   -- jobs done per worker
  return 1
end
return 0
"""

# reap: for every expired lease, requeue (original score) or give up.
# The whole script is one atomic step, so a second reaper finds the leases
# already removed and does nothing -> never a double requeue.
_REAP_LUA = """
-- KEYS: processing, owner, jobs, counters     ARGV: max_retries, ns
local t = redis.call('TIME')
local now = t[1] + t[2] / 1000000
local requeued, failed = 0, 0
local leases = redis.call('HGETALL', KEYS[1])            -- {id, deadline, id, deadline...}
for i = 1, #leases, 2 do
  local id, deadline = leases[i], tonumber(leases[i + 1])
  if deadline < now then
    redis.call('HDEL', KEYS[1], id)
    redis.call('HDEL', KEYS[2], id)
    local raw = redis.call('GET', ARGV[2] .. 'job:' .. id)
    if raw and redis.call('EXISTS', ARGV[2] .. 'result:' .. id) == 0 then
      local job = cjson.decode(raw)
      local n = redis.call('INCR', ARGV[2] .. 'retries:' .. id)
      redis.call('HINCRBY', KEYS[4], 'retries_total', 1)
      if n <= tonumber(ARGV[1]) then
        redis.call('ZADD', KEYS[3], job.score, id)          -- back in line, same priority
        requeued = requeued + 1
      else
        redis.call('SET', ARGV[2] .. 'result:' .. id, cjson.encode({
          event_id = id, worker_id = '', enqueued_at = job.enqueued_at,
          started_at = '', finished_at = tostring(now), retries = n,
          risk = job.risk, is_critical = job.is_critical, llm_mode = '',
          resolution_status = 'failed', fallbacks_used = 0 }))
        redis.call('HINCRBY', KEYS[4], 'failed', 1)
        failed = failed + 1
      end
    end
  end
end
return {requeued, failed}
"""


# record: keep the full negotiation record of a finished job, bounded in size.
#   transcript:<id>     JSON {summary, transcript, resolution}, expires after TTL (2 h)
#   recent_transcripts  LIST of summary JSON, newest first, at most WINDOW entries
#   recent_critical     LIST of summary JSON of critical jobs, newest first, at most 10
# Pushing and trimming happen in one atomic script. A job that falls out of the window loses its
# transcript key (unless it was critical: criticals keep theirs until the TTL), so memory stays
# bounded however big the storm is.
_RECORD_LUA = """
-- KEYS: transcript, recent_transcripts, recent_critical
-- ARGV: record_json, summary_json, is_critical('1'/'0'), ttl_seconds, window, ns, critical_cap
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[4]))
redis.call('LPUSH', KEYS[2], ARGV[2])
local window = tonumber(ARGV[5])
for _, raw in ipairs(redis.call('LRANGE', KEYS[2], window, -1)) do      -- entries beyond the window
  local row = cjson.decode(raw)
  if not row.is_critical then redis.call('DEL', ARGV[6] .. 'transcript:' .. row.event_id) end
end
redis.call('LTRIM', KEYS[2], 0, window - 1)
if ARGV[3] == '1' then
  redis.call('LPUSH', KEYS[3], ARGV[2])
  redis.call('LTRIM', KEYS[3], 0, tonumber(ARGV[7]) - 1)
end
return 1
"""

TRANSCRIPT_TTL_S = 2 * 3600
TRANSCRIPT_WINDOW = 30
CRITICAL_KEEP = 10


class Queue:
    def __init__(self, url: str | None = None, ns: str = "",
                 lease_seconds: float | None = None, max_retries: int | None = None) -> None:
        # Timeouts turn a hung/dead connection into a TimeoutError/ConnectionError that the
        # callers retry (see resilience.py); without them a half-open socket could block forever.
        self.r = redis.Redis.from_url(
            url or os.environ.get("REDIS_URL", "redis://redis:6379"), decode_responses=True,
            socket_timeout=5, socket_connect_timeout=2)
        self.ns = ns
        self.lease_seconds = float(lease_seconds if lease_seconds is not None
                                   else os.environ.get("LEASE_SECONDS", "30"))
        self.max_retries = int(max_retries if max_retries is not None
                               else os.environ.get("MAX_RETRIES", "3"))
        self._pop = self.r.register_script(_POP_LUA)
        self._complete = self.r.register_script(_COMPLETE_LUA)
        self._reap = self.r.register_script(_REAP_LUA)
        self._record = self.r.register_script(_RECORD_LUA)

    def k(self, name: str) -> str:
        return self.ns + name

    def now(self) -> float:
        s, us = self.r.time()
        return s + us / 1e6

    # --- producer -----------------------------------------------------
    def enqueue(self, job: Job, score: float) -> bool:
        """Idempotent: a job that already has a result is not queued again."""
        if self.r.exists(self.k("result:" + job.event_id)):
            return False
        payload = job.model_dump()
        payload["score"] = score
        pipe = self.r.pipeline()
        pipe.set(self.k("job:" + job.event_id), json.dumps(payload))
        pipe.zadd(self.k("jobs"), {job.event_id: score})
        pipe.execute()
        return True

    def submit(self, jobs: list[Job], policy: str) -> int:
        """Stamp enqueued_at = now, score by `policy`, enqueue in one pipelined burst.
        Jobs that already have a result are skipped. Returns how many were queued."""
        done = self.r.mget([self.k("result:" + j.event_id) for j in jobs]) if jobs else []
        pipe = self.r.pipeline()
        queued = 0
        for job, existing in zip(jobs, done):
            if existing is not None:
                continue
            job.enqueued_at = datetime.now(timezone.utc).isoformat()  # distinct per job -> stable fifo
            payload = job.model_dump()
            payload["score"] = compute_score(job, policy)
            pipe.set(self.k("job:" + job.event_id), json.dumps(payload))
            pipe.zadd(self.k("jobs"), {job.event_id: payload["score"]})
            queued += 1
        pipe.execute()
        return queued

    # --- worker side --------------------------------------------------
    def pop(self, worker_id: str) -> dict | None:
        """Lease the best job. Returns the stored job dict (with 'score') or None."""
        job_id = self._pop(keys=[self.k("jobs"), self.k("processing"), self.k("owner")],
                           args=[self.lease_seconds, worker_id, self.ns])
        if not job_id:
            return None
        raw = self.r.get(self.k("job:" + job_id))
        return json.loads(raw) if raw else None

    def retries(self, job_id: str) -> int:
        return int(self.r.get(self.k("retries:" + job_id)) or 0)

    def complete(self, job_id: str, result: dict) -> bool:
        """True if this call stored the result; False if one already existed (kept)."""
        field = "failed" if result.get("resolution_status") == "failed" else "completed"
        keys = [self.k(n) for n in ("result:" + job_id, "processing", "owner", "jobs", "counters",
                                    "recent", "worker_done")]
        return bool(self._complete(keys=keys, args=[json.dumps(result), job_id, field,
                                                    result.get("worker_id", "")]))

    def record(self, summary: dict, transcript: list, resolution: dict) -> None:
        """Store the full record of a job whose complete() this worker just won (see _RECORD_LUA)."""
        jid = summary["event_id"]
        self._record(
            keys=[self.k("transcript:" + jid), self.k("recent_transcripts"), self.k("recent_critical")],
            args=[json.dumps({"summary": summary, "transcript": transcript, "resolution": resolution}),
                  json.dumps(summary), "1" if summary.get("is_critical") else "0",
                  TRANSCRIPT_TTL_S, TRANSCRIPT_WINDOW, self.ns, CRITICAL_KEEP])

    def recent_summaries(self, limit: int) -> list[dict]:
        """Newest-first summary rows for the UI: the last `limit` completed jobs, plus up to 3 of the
        newest critical jobs that already scrolled out of that view (so the priority lane stays
        visible after a storm). One pipelined round trip: two LRANGE, no SCAN/KEYS."""
        pipe = self.r.pipeline(transaction=False)
        pipe.lrange(self.k("recent_transcripts"), 0, limit - 1)
        pipe.lrange(self.k("recent_critical"), 0, 2)
        newest, crit = pipe.execute()
        rows = [json.loads(x) for x in newest]
        shown = {r["event_id"] for r in rows}
        pinned = [c for c in (json.loads(x) for x in crit) if c["event_id"] not in shown]
        return (rows[:max(0, limit - len(pinned))] + pinned)[:limit]

    def get_record(self, job_id: str) -> dict | None:
        raw = self.r.get(self.k("transcript:" + job_id))
        return json.loads(raw) if raw else None

    def reap(self) -> tuple[int, int]:
        """Requeue (or fail) expired leases. Returns (requeued, failed)."""
        requeued, failed = self._reap(
            keys=[self.k(n) for n in ("processing", "owner", "jobs", "counters")],
            args=[self.max_retries, self.ns])
        return int(requeued), int(failed)

    def heartbeat(self, worker_id: str) -> None:
        self.r.hset(self.k("workers"), worker_id, self.now())

    def deregister(self, worker_id: str) -> None:
        self.r.hdel(self.k("workers"), worker_id)

    # --- inspection ---------------------------------------------------
    def get_result(self, job_id: str) -> dict | None:
        raw = self.r.get(self.k("result:" + job_id))
        return json.loads(raw) if raw else None

    def stats_snapshot(self) -> dict:
        now = self.now()
        counters = self.r.hgetall(self.k("counters"))
        beats = {w: now - float(t) for w, t in self.r.hgetall(self.k("workers")).items()}
        return {
            "depth": self.r.zcard(self.k("jobs")),
            "processing": self.r.hlen(self.k("processing")),
            "completed": int(counters.get("completed", 0)),
            "failed": int(counters.get("failed", 0)),
            "retries_total": int(counters.get("retries_total", 0)),
            "worker_heartbeat_age_s": beats,  # worker_id -> seconds since last beat
        }
