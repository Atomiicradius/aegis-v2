"""
timeline.py — system-level event log.

Redis contract (global keys, no namespace prefix):
  timeline                LIST   LPUSH + LTRIM to 2000; newest first
  timeline:seq            STRING INCR for monotonic ordering
  timeline:outage:<ts_s>  STRING (NX, 120 s TTL) — dedup redis_down per second

Kill switch: TIMELINE=0 disables all writes (default on).

emit() never raises and never blocks. If Redis is unreachable, events are buffered
in memory (up to 200, with original timestamps) and flushed on the next successful call.
redis_down/redis_up are deduplicated across workers via SET NX on the outage key.
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
# Track outage timestamps already in the buffer (in-process dedup while Redis is down).
_outage_buffered: set[int] = set()


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
    """Flush buffered events (oldest first). For redis_down events, check dedup NX key first."""
    with _buffer_lock:
        pending = list(_buffer)
        pending_len = len(pending)
    if not pending:
        return
    for event in pending:
        if event.get("type") == "redis_down":
            ts_key = int(event.get("ts", 0))
            dedup_key = f"timeline:outage:{ts_key}"
            if not r.set(dedup_key, "1", nx=True, ex=120):
                continue  # another process already claimed this outage second
        _write_one(r, event)
    with _buffer_lock:
        del _buffer[:pending_len]
        _outage_buffered.clear()


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


def emit_redis_down(r, ts_down: float) -> None:
    """Emit redis_down event, deduped per second across workers via SET NX."""
    if not TIMELINE_ON:
        return
    ts_key = int(ts_down)
    dedup_key = f"timeline:outage:{ts_key}"
    event: dict[str, Any] = {
        "seq": 0, "ts": ts_down, "type": "redis_down", "source": "worker", "detail": {},
    }
    try:
        if r.set(dedup_key, "1", nx=True, ex=120):
            _write_one(r, event)
    except RedisError:
        with _buffer_lock:
            if ts_key not in _outage_buffered and len(_buffer) < _BUFFER_MAX:
                _outage_buffered.add(ts_key)
                _buffer.append(event)


def emit_redis_up(r, ts_down: float) -> None:
    """Emit redis_up. Called right after recovery; also flushes the buffer (which has redis_down)."""
    if not TIMELINE_ON:
        return
    emit(r, "redis_up", "worker", detail={"outage_started_ts": ts_down})


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
