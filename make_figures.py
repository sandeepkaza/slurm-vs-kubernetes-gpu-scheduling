"""Paper 6 figures (vector PDF, IEEE single-column 3.5in).

Fig 1 (fig_wait_cdf.pdf): queue-wait CDF per stack -- the fairness story in one
      curve (Slurm even; K8s default splits narrow-fast / gang-slow; Kueue between).
Fig 2 (fig_width.pdf): median + p95 wait by GPU width, Slurm vs K8s vs Kueue --
      the gang-starvation result.
Fig 3 (fig_preempt.pdf): RQ4 -- job completion by stack (Slurm all vs K8s 4/40),
      and the priority speedup Slurm delivers.

Reads data/results_*.csv. Run: python make_figures.py
"""

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA = Path(__file__).parent / "data"
NS = 1e9

# Okabe-Ito
SLURM, K8S, KUEUE = "#0072B2", "#D55E00", "#009E73"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#d9d8d3"

plt.rcParams.update({
    "font.size": 7.5, "axes.labelsize": 7.5, "axes.titlesize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
    "font.family": "serif", "axes.edgecolor": MUTED, "axes.linewidth": 0.6,
    "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
    "axes.labelcolor": INK, "pdf.fonttype": 42,
})


def waits(path):
    """(id, gpus, priority, wait_s) for rows with complete timing."""
    out = []
    for r in csv.DictReader(open(DATA / path, encoding="utf-8")):
        if not r.get("start_ns"):
            continue
        w = (int(r["start_ns"]) - int(r["submit_ns"])) / NS
        # guard against any stale-timing rows (belt and suspenders)
        if -1 < w < 100000:
            out.append((r["id"], int(r["gpus"]), int(r["priority"]), w))
    return out


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return float("nan") if n == 0 else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def p95(xs):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = int(round(0.95 * (len(xs) - 1)))
    return xs[k]


slurm = waits("results_slurm.csv")
k8s = waits("results_k8s.csv")
kueue = waits("results_k8s_kueue.csv")

# ---------- Fig 1: wait CDF ----------
fig, ax = plt.subplots(figsize=(3.5, 2.3))
for data, color, label in [(slurm, SLURM, "Slurm"), (k8s, K8S, "K8s default"),
                           (kueue, KUEUE, "K8s + Kueue")]:
    ws = sorted(w for _, _, _, w in data)
    ys = [(i + 1) / len(ws) for i in range(len(ws))]
    ax.step(ws, ys, where="post", color=color, linewidth=1.4, label=label)
ax.set_xlabel("Queue wait (s)")
ax.set_ylabel("Fraction of jobs $\\leq$ wait")
ax.set_ylim(0, 1.02)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(True, linewidth=0.3, color=GRID)
ax.set_axisbelow(True)
ax.legend(frameon=False, loc="lower right")
fig.tight_layout()
fig.savefig("fig_wait_cdf.pdf")
print("fig_wait_cdf.pdf")

# ---------- Fig 2: wait by width ----------
fig, ax = plt.subplots(figsize=(3.5, 2.3))
stacks = [("Slurm", slurm, SLURM), ("K8s", k8s, K8S), ("Kueue", kueue, KUEUE)]
widths = [1, 2]
x = range(len(stacks))
w = 0.36
for wi, width in enumerate(widths):
    meds = [median([ww for _, g, _, ww in d if g == width]) for _, d, _ in stacks]
    p95s = [p95([ww for _, g, _, ww in d if g == width]) for _, d, _ in stacks]
    off = (wi - 0.5) * w
    bars = ax.bar([i + off for i in x], meds, w,
                  color=[c for _, _, c in stacks], alpha=0.55 if width == 1 else 1.0,
                  hatch="" if width == 1 else "///", edgecolor="white", linewidth=0.3,
                  label=f"{width}-GPU median")
    # p95 whisker
    for i, (m, p) in enumerate(zip(meds, p95s)):
        ax.plot([i + off, i + off], [m, p], color=MUTED, linewidth=0.8)
        ax.plot([i + off - 0.06, i + off + 0.06], [p, p], color=MUTED, linewidth=0.8)
    for i, m in enumerate(meds):
        ax.annotate(f"{m:.0f}", (i + off, m), ha="center", va="bottom",
                    fontsize=6, color=MUTED)
ax.set_xticks(list(x), [s for s, _, _ in stacks])
ax.set_ylabel("Queue wait (s)")
ax.set_title("bar = median, whisker to p95;  solid = 2-GPU gang", fontsize=6.5)
ax.spines[["top", "right"]].set_visible(False)
ax.yaxis.grid(True, linewidth=0.3, color=GRID)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig("fig_width.pdf")
print("fig_width.pdf")

# ---------- Fig 3: RQ4 preemption ----------
fig, (axL, axR) = plt.subplots(1, 2, figsize=(3.5, 2.0), gridspec_kw={"wspace": 0.55})

# left: completion rate
sp = waits("results_slurm_preempt.csv")
kp_rows = list(csv.DictReader(open(DATA / "results_k8s_preempt.csv", encoding="utf-8")))
kp_done = sum(1 for r in kp_rows if r.get("start_ns"))
comp = [("Slurm", 40, SLURM), ("K8s", kp_done, K8S)]
axL.bar([0, 1], [c for _, c, _ in comp], 0.6, color=[c for _, _, c in comp])
for i, (_, c, _) in enumerate(comp):
    axL.annotate(f"{c}/40", (i, c), ha="center", va="bottom", fontsize=7, color=MUTED)
axL.set_xticks([0, 1], [s for s, _, _ in comp])
axL.set_ylabel("Jobs completed (of 40)")
axL.set_ylim(0, 46)
axL.set_title("preempt: survival", fontsize=7)
axL.spines[["top", "right"]].set_visible(False)
axL.yaxis.grid(True, linewidth=0.3, color=GRID)
axL.set_axisbelow(True)

# right: Slurm priority speedup (baseline vs preempt, high-prio median)
base = waits("results_slurm.csv")
hi_base = median([w for _, _, p, w in base if p == 1])
hi_pre = median([w for _, _, p, w in sp if p == 1])
lo_pre = median([w for _, _, p, w in sp if p == 0])
axR.bar([0, 1, 2], [hi_base, lo_pre, hi_pre], 0.6, color=[MUTED, SLURM, SLURM])
for i, v in enumerate([hi_base, lo_pre, hi_pre]):
    axR.annotate(f"{v:.0f}", (i, v), ha="center", va="bottom", fontsize=6.5, color=MUTED)
axR.set_xticks([0, 1, 2], ["hi\n(no pre)", "lo\n(pre)", "hi\n(pre)"], fontsize=6.3)
axR.set_ylabel("Median wait (s)")
axR.set_title("Slurm priority", fontsize=7)
axR.spines[["top", "right"]].set_visible(False)
axR.yaxis.grid(True, linewidth=0.3, color=GRID)
axR.set_axisbelow(True)
fig.tight_layout()
fig.savefig("fig_preempt.pdf")
print("fig_preempt.pdf")

# ---------- Fig 4: reordering scatter (submit rank vs start rank) ----------
def ranks(path):
    rows = [r for r in csv.DictReader(open(DATA / path, encoding="utf-8")) if r.get("start_ns")]
    by_sub = sorted(rows, key=lambda r: int(r["submit_ns"]))
    sub_rank = {r["id"]: i for i, r in enumerate(by_sub)}
    by_start = sorted(rows, key=lambda r: int(r["start_ns"]))
    return [(sub_rank[r["id"]], i, int(r["gpus"])) for i, r in enumerate(by_start)]


fig, axes = plt.subplots(1, 3, figsize=(7.1, 2.4), sharex=True, sharey=True)
for ax, (path, title, col) in zip(axes, [
        ("results_slurm.csv", "Slurm (6.3% inv.)", SLURM),
        ("results_k8s.csv", "K8s default (40.0% inv.)", K8S),
        ("results_k8s_kueue.csv", "K8s + Kueue (16.5% inv.)", KUEUE)]):
    pts = ranks(path)
    ax.plot([0, 39], [0, 39], color=MUTED, linewidth=0.5, linestyle=":")
    for sr, str_, g in pts:
        ax.scatter(sr, str_, s=22 if g == 2 else 9,
                   marker="s" if g == 2 else "o",
                   color=col, alpha=0.85 if g == 2 else 0.45,
                   edgecolor="white" if g == 2 else "none", linewidth=0.3)
    ax.set_title(title, fontsize=7)
    ax.set_xlabel("submit order")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(True, linewidth=0.3, color=GRID)
    ax.set_axisbelow(True)
axes[0].set_ylabel("start order")
axes[0].annotate("2-GPU (square) far below the\ndiagonal = jumped by later jobs",
                 (2, 34), fontsize=5.6, color=MUTED)
fig.tight_layout()
fig.savefig("fig_reorder.pdf")
print("fig_reorder.pdf")

print(f"\n[check] slurm hi no-pre {hi_base:.0f}s -> pre {hi_pre:.0f}s "
      f"({hi_base/hi_pre:.1f}x); K8s preempt completed {kp_done}/40")
