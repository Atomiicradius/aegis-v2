"""
timeline.py — system-level event log.

Redis contract (global keys, no namespace prefix):
  timeline               LIST   LPUSH + LTRIM to 2000; newest first
  timeline:seq           STRING INCR for monotonic ordering
  timeline:outage:lock   STRING SET NX, 7 s TTL — one per event type, dedup redis_down
  timeline:recovery:lock STRING SET NX, 7 s TTL — one per event type, dedup redis_up

Kill switch: TIMELINE=0 disables all writes (default on).

emit() never raises and never blocks. If Redis is unreachable, events are buffered
in memory (up to 200, with original timestamps) and flushed on the next successful call.
redis_down/redis_up are deduplicated across workers via SET NX on a per-event-type lock.
Two outages more than _OUTAGE_LOCK_TTL seconds apart each produce their own pair.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from redis.exceptions import RedisError

TIMELINE_ON = os.environ.get("TIMELINE", "1") != "0"
_BUFFER_MAX = 200
_LIST_CAP = 2000

_buffer: list[dict] = []
_buffer_lock = threading.Lock()
# In-process dedup while Redis is down: True means we already have that event type buffered.
_outage_buffered: bool = False
_recovery_buffered: bool = False


def _now_ts() -> float:
    return time.time()


def _write_one(r, event: dict) -> None:
    """Get a seq number, write one event to the capped list. NOT safe to call alone while flushing."""
    seq = r.incr("timeline:seq")
    e = dict(event)
    e["seq"] = seq
    pipe = r.pipeline()
    pipe.lpush("timeline", json.dumps(e))
    pipe.ltrim("timeline", 0, _LIST_CAP - 1)
    pipe.execute()


def _flush_buffer(r) -> None:
    """Flush buffered events (oldest first). Dedup redis_down and redis_up via SET NX lock."""
    global _outage_buffered, _recovery_buffered
    with _buffer_lock:
        pending = list(_buffer)
        pending_len = len(pending)
    if not pending:
        return
    for event in pending:
        if event.get("type") == "redis_down":
            if not r.set("timeline:outage:lock", "1", nx=True, ex=_OUTAGE_LOCK_TTL):
                continue
        elif event.get("type") == "redis_up":
            if not r.set("timeline:recovery:lock", "1", nx=True, ex=_OUTAGE_LOCK_TTL):
                continue
        _write_one(r, event)
    with _buffer_lock:
        del _buffer[:pending_len]
        _outage_buffered = False
        _recovery_buffered = False


def emit(r, type_: str, source: str, detail: dict | None = None,
         event_id: str | None = None, ts: float | None = None) -> None:
    """Emit a timeline event. Best-effort: never raises, never blocks."""
    if not TIMELINE_ON:
        return
    event: dict[str, Any] = {
        "seq": 0,
        "ts": ts if ts is not None else _now_ts(),
        "type": type_,
        "source": source,
        "detail": detail or {},
    }
    if event_id is not None:
        event["event_id"] = event_id

    try:
        _flush_buffer(r)
        _write_one(r, event)
    except RedisError:
        with _buffer_lock:
            if len(_buffer) < _BUFFER_MAX:
                _buffer.append(event)


_OUTAGE_LOCK_TTL = 7  # seconds; two outages >7 s apart each get their own pair


def emit_redis_down(r, ts_down: float) -> None:
    """Emit redis_down, deduped with a per-type SET NX lock (TTL=_OUTAGE_LOCK_TTL)."""
    global _outage_buffered
    if not TIMELINE_ON:
        return
    event: dict[str, Any] = {
        "seq": 0, "ts": ts_down, "type": "redis_down", "source": "worker", "detail": {},
    }
    try:
        if r.set("timeline:outage:lock", "1", nx=True, ex=_OUTAGE_LOCK_TTL):
            _write_one(r, event)
    except RedisError:
        with _buffer_lock:
            if not _outage_buffered and len(_buffer) < _BUFFER_MAX:
                _outage_buffered = True
                _buffer.append(event)


def emit_redis_up(r, ts_down: float) -> None:
    """Emit redis_up, deduped with a per-type SET NX lock. Flushes buffer first."""
    global _recovery_buffered
    if not TIMELINE_ON:
        return
    event: dict[str, Any] = {
        "seq": 0, "ts": _now_ts(), "type": "redis_up", "source": "worker",
        "detail": {"outage_started_ts": ts_down},
    }
    try:
        _flush_buffer(r)  # flush redis_down (and any other buffered events) first
        if r.set("timeline:recovery:lock", "1", nx=True, ex=_OUTAGE_LOCK_TTL):
            _write_one(r, event)
    except RedisError:
        with _buffer_lock:
            if not _recovery_buffered and len(_buffer) < _BUFFER_MAX:
                _recovery_buffered = True
                _buffer.append(event)


def get_events(r, since_seq: int = 0, limit: int = 200) -> list[dict]:
    """Return events in ascending seq order with seq > since_seq, up to limit."""
    raws = r.lrange("timeline", 0, _LIST_CAP - 1)
    events: list[dict] = []
    for raw in reversed(raws):  # oldest first
        try:
            e = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if int(e.get("seq", 0)) > since_seq:
            events.append(e)
    return events[-limit:]
