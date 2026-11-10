"""Print queue state: depth, in-flight, completed, failed, retries, live/dead workers."""
import _common  # noqa: F401
from backend.cloud.queue import Queue

LIVE_BELOW_S = 10.0

s = Queue().stats_snapshot()
beats = s["worker_heartbeat_age_s"]
live = sorted(w for w, age in beats.items() if age < LIVE_BELOW_S)
dead = sorted(w for w, age in beats.items() if age >= LIVE_BELOW_S)
dead_desc = [w + " (silent " + str(round(beats[w])) + "s)" for w in dead]
print("queue depth:   ", s["depth"])
print("processing:    ", s["processing"])
print("completed:     ", s["completed"])
print("failed:        ", s["failed"])
print("retries total: ", s["retries_total"])
print("live workers:  ", len(live), live)
print("dead workers:  ", len(dead), dead_desc)
