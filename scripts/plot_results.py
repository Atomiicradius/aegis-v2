"""Plot results/benchmark.csv -> two PNGs (host, needs matplotlib: pip install matplotlib)."""
import csv
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("matplotlib missing: pip install matplotlib")

REPO = Path(__file__).resolve().parent.parent
RES = REPO / "results"
rows = list(csv.DictReader(open(RES / "benchmark.csv", newline="")))
if not rows:
    sys.exit("results/benchmark.csv is empty")

events, stub = rows[0]["events"], rows[0]["stub_ms"]
setup = f"stub {stub} ms, N={events} events + {rows[0]['critical']} critical"
policies = ["fifo", "priority"]
colors = {"fifo": "#d62728", "priority": "#1f77b4"}


def series(policy: str, col: str) -> tuple[list[int], list[float]]:
    pts = sorted((int(r["workers"]), float(r[col])) for r in rows if r["policy"] == policy)
    return [p[0] for p in pts], [p[1] for p in pts]


# 1. throughput vs workers
fig, ax = plt.subplots(figsize=(7, 4.5))
for pol in policies:
    xs, ys = series(pol, "throughput_jobs_s")
    if xs:
        ax.plot(xs, ys, "o-", label=pol, color=colors[pol])
xs, ys = series("fifo", "throughput_jobs_s") or series("priority", "throughput_jobs_s")
if xs:
    ax.plot(xs, [ys[0] / xs[0] * x for x in xs], "k--", label=f"ideal linear (from {xs[0]} worker)")
ax.set_xlabel("workers")
ax.set_ylabel("throughput (jobs/s)")
ax.set_title(f"Throughput vs workers\n{setup}")
ax.set_xticks(sorted({int(r["workers"]) for r in rows}))
ax.grid(alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig(RES / "throughput_vs_workers.png", dpi=150)

# 2. critical-job latency, fifo vs priority (grouped bars)
ws = sorted({int(r["workers"]) for r in rows})
val = {(p, int(r["workers"])): float(r["crit_avg_ms"]) for r in rows for p in [r["policy"]]}
fig, ax = plt.subplots(figsize=(7, 4.5))
width = 0.38
for i, pol in enumerate(policies):
    heights = [val.get((pol, w), 0) for w in ws]
    bars = ax.bar([k + (i - 0.5) * width for k in range(len(ws))], heights, width, label=pol, color=colors[pol])
    ax.bar_label(bars, fmt="%.0f", fontsize=8)
positive = [v for v in val.values() if v > 0]
if positive and max(positive) / min(positive) > 10:
    ax.set_yscale("log")
ax.set_xticks(range(len(ws)), [str(w) for w in ws])
ax.set_xlabel("workers")
ax.set_ylabel("critical job latency, avg (ms)")
ax.set_title(f"Critical-event latency: FIFO vs priority\n{setup}")
ax.grid(alpha=0.3, axis="y")
ax.legend()
fig.tight_layout()
fig.savefig(RES / "critical_latency_fifo_vs_priority.png", dpi=150)
print("wrote", RES / "throughput_vs_workers.png", "and", RES / "critical_latency_fifo_vs_priority.png")
