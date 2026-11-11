"""
trace.py — per-job execution trace (attempt-level timing waterfall).

Redis keys (all prefixed with `ns`):
  trace:<id>       STRING  full trace JSON, TTL 1 h
  recent_retried   LIST    summaries of last 10 jobs with >1 attempt, newest first

Kill switch: RECORD_TRACES=0 disables writes (default on).
All writes are best-effort: a failure never affects a job's result or delivery.

Trace structure:
  {
    "enqueued_at": "<iso>",
    "risk": 0.0,
    "is_critical": false,
    "attempts": [
      {
        "n": 1,
        "worker_id": "...",
        "leased_at": 1234567.890,      # unix float (Redis TIME)
        "lease_deadline": 1234597.890,
        "outcome": "pending|completed|lease_expired|error|duplicate_discarded|dead_lettered",
        "process_start_ms": 12.3,      # ms after leased_at (perf_counter offset)
        "process_end_ms": 345.6,
        "negotiate_ms": 310.0          # time for engine.run_negotiation()
      }
    ],
    "completed_at": "<iso>",
    "final_status": "approved|..."
  }
"""

from __future__ import annotations

import json
import os
from typing import Any

from redis.exceptions import RedisError

RECORD_TRACES = os.environ.get("RECORD_TRACES", "1") != "0"
TRACE_TTL_S = 3600          # 1 hour
RETRIED_CAP = 10            # recent_retried list length

# Atomically create or extend the trace with a new attempt.
# Attempt number = #trace.attempts + 1 (sequential, matches reaper's retry counter).
_START_LUA = """
-- KEYS[1]: trace:<id>
-- ARGV: enqueued_at, risk, is_critical('1'/'0'), worker_id, leased_at, lease_deadline, ttl
local raw = redis.call('GET', KEYS[1])
local trace
if raw then
  trace = cjson.decode(raw)
else
  trace = {
    enqueued_at = ARGV[1],
    risk        = tonumber(ARGV[2]),
    is_critical = (ARGV[3] == '1'),
    attempts    = {},
    completed_at = '',
    final_status = ''
  }
end
local n = #trace.attempts + 1
table.insert(trace.attempts, {
  n             = n,
  worker_id     = ARGV[4],
  leased_at     = tonumber(ARGV[5]),
  lease_deadline = tonumber(ARGV[6]),
  outcome        = 'pending',
  process_start_ms = 0,
  process_end_ms   = 0,
  negotiate_ms     = 0
})
redis.call('SET', KEYS[1], cjson.encode(trace), 'EX', tonumber(ARGV[7]))
return n
"""

# Atomically update one attempt's outcome and timings.
_FINISH_LUA = """
-- KEYS[1]: trace:<id>
-- ARGV: attempt_n, outcome, timings_json, ttl, completed_at ('' if none), final_status ('' if none)
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local trace = cjson.decode(raw)
local n = tonumber(ARGV[1])
if trace.attempts[n] then
  trace.attempts[n].outcome = ARGV[2]
  local ok, timings = pcall(cjson.decode, ARGV[3])
  if ok and type(timings) == 'table' then
    for k, v in pairs(timings) do trace.attempts[n][k] = v end
  end
  if ARGV[5] ~= '' then trace.completed_at = ARGV[5] end
  if ARGV[6] ~= '' then trace.final_status = ARGV[6] end
end
redis.call('SET', KEYS[1], cjson.encode(trace), 'EX', tonumber(ARGV[4]))
return 1
"""


class Tracer:
    """Best-effort per-job trace recorder. Errors are silently swallowed."""

    def __init__(self, r, ns: str = "") -> None:
        self.r = r
        self.ns = ns
        if RECORD_TRACES:
            self._start_script = r.register_script(_START_LUA)
            self._finish_script = r.register_script(_FINISH_LUA)

    def _k(self, name: str) -> str:
        return self.ns + name

    def start_attempt(self, event_id: str, enqueued_at: str, risk: float, is_critical: bool,
                      worker_id: str, leased_at: float, lease_deadline: float) -> int:
        """Create or extend the trace with a new attempt. Returns attempt_n (1-based), or 0 on failure."""
        if not RECORD_TRACES:
            return 0
        try:
            return int(self._start_script(
                keys=[self._k(f"trace:{event_id}")],
                args=[enqueued_at, str(round(risk, 6)), "1" if is_critical else "0",
                      worker_id, str(leased_at), str(lease_deadline), str(TRACE_TTL_S)]))
        except (RedisError, Exception):
            return 0

    def finish_attempt(self, event_id: str, attempt_n: int, outcome: str,
                       timings: dict[str, Any] | None = None,
                       completed_at: str = "", final_status: str = "") -> None:
        """Update an attempt's outcome and optional timings. Best-effort."""
        if not RECORD_TRACES or attempt_n <= 0:
            return
        try:
            self._finish_script(
                keys=[self._k(f"trace:{event_id}")],
                args=[str(attempt_n), outcome, json.dumps(timings or {}),
                      str(TRACE_TTL_S), completed_at, final_status])
            # Push to recent_retried when a job needed more than one attempt.
            if outcome == "completed" and attempt_n > 1:
                raw = self.r.get(self._k(f"trace:{event_id}"))
                if raw:
                    trace = json.loads(raw)
                    summary = {
                        "event_id": event_id,
                        "attempts": attempt_n,
                        "is_critical": trace.get("is_critical", False),
                        "final_status": final_status,
                        "completed_at": completed_at,
                    }
                    pipe = self.r.pipeline()
                    pipe.lpush(self._k("recent_retried"), json.dumps(summary))
                    pipe.ltrim(self._k("recent_retried"), 0, RETRIED_CAP - 1)
                    pipe.execute()
        except (RedisError, Exception):
            pass

    def get(self, event_id: str) -> dict | None:
        """Return the full trace dict, or None if expired/unknown."""
        try:
            raw = self.r.get(self._k(f"trace:{event_id}"))
            return json.loads(raw) if raw else None
        except (RedisError, Exception):
            return None

    def recent_retried(self, limit: int = 5) -> list[dict]:
        """Return up to `limit` recent summaries of jobs that required >1 attempt."""
        try:
            raws = self.r.lrange(self._k("recent_retried"), 0, limit - 1)
            return [json.loads(x) for x in raws]
        except (RedisError, Exception):
            return []
