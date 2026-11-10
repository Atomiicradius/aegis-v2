"""Plot results/chaos.csv -> results/chaos_recovery.png (host, needs matplotlib: pip install matplotlib).

One bar per scenario = median time from fault to drained queue; a diamond marks the worst run;
the label above says how many jobs were lost (worst run) and how many runs there were.
"""
import csv
import statistics
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("matplotlib missing: pip install matplotlib")

REPO = Path(__file__).resolve().parent.parent
SRC, DST = REPO / "results" / "chaos.csv", REPO / "results" / "chaos_recovery.png"
if not SRC.exists():
    sys.exit("results/chaos.csv not found: run python scripts/chaos.py first")

LABELS = {"kill1": "kill 1 of 4\nworkers", "kill2": "kill 2 of 4\nworkers", "kill3": "kill 3 of 4\nworkers",
          "redis_restart": "graceful\nredis restart", "redis_kill": "hard redis\nkill (docker kill)"}
COLORS = {"kill1": "#1f77b4", "kill2": "#1f77b4", "kill3": "#1f77b4", "redis_restart": "#ff7f0e", "redis_kill": "#d62728"}

rows = list(csv.DictReader(open(SRC, newline="", encoding="utf-8")))
# the plot describes ONE setup: use the most common event count (e.g. ignore an old --quick N=200 batch)
events = statistics.mode([r["events"] for r in rows])
rows = [r for r in rows if r["events"] == events]
order = [s for s in LABELS if any(r["scenario"] == s for r in rows)]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


med, worst, lost, runs = [], [], [], []
for s in order:
    rs = [r for r in rows if r["scenario"] == s]
    drains = [d for d in (num(r["drain_s"]) for r in rs) if d is not None]
    med.append(statistics.median(drains) if drains else 0)
    worst.append(max(drains) if drains else 0)
    lost.append(max((num(r["jobs_lost"]) or 0) for r in rs))
    runs.append(len(rs))

fig, ax = plt.subplots(figsize=(9, 5))
bars = ax.bar(range(len(order)), med, 0.55, color=[COLORS[s] for s in order], label="median time to drain")
ax.scatter(range(len(order)), worst, marker="D", s=60, color="black", zorder=3, label="worst run")
top = max(worst + [1])
for i, (m, w, lo, n) in enumerate(zip(med, worst, lost, runs)):
    ax.text(i, max(m, w) + top * 0.03, f"lost: {lo:.0f}\n({n} run{'s' if n != 1 else ''})", ha="center", va="bottom", fontsize=9)
ax.set_ylim(0, top * 1.22)
ax.set_xticks(range(len(order)), [LABELS[s] for s in order])
ax.set_ylabel("seconds from fault to drained queue")
ax.set_title(f"Recovery after a fault\n4 workers, stub 150 ms, {events} events, lease 10 s (fault ~6 s into the storm)")
ax.grid(alpha=0.3, axis="y")
ax.legend(loc="upper left")
fig.tight_layout()
fig.savefig(DST, dpi=150)
print("wrote", DST)
