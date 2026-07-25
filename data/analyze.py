"""Paper 6 analysis: queue wait, makespan, utilization, gang behaviour.

Reads the per-leg CSVs written by run_slurm.py / run_k8s.py and prints the
RQ1-RQ4 aggregates that results.md is built from. All timings come from the
same two points on both stacks: submit_ns (harness) and start_ns/end_ns (the
workload's own ns timestamps), so no scheduler's self-reported accounting is
trusted for the headline comparison.

Usage: python3 analyze.py results_slurm.csv results_k8s.csv [more.csv ...]
"""

import csv
import statistics as st
import sys
from pathlib import Path

NS = 1e9


def load(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if not r.get("start_ns") or not r.get("end_ns"):
                r["_incomplete"] = True
            rows.append(r)
    return rows


def q(vals, p):
    if not vals:
        return float("nan")
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def leg(path):
    rows = load(path)
    ok = [r for r in rows if not r.get("_incomplete")]
    name = Path(path).stem
    print(f"\n### {name}  ({len(ok)}/{len(rows)} jobs with complete timings)")
    if not ok:
        print("  no usable rows")
        return

    waits, runs = [], []
    for r in ok:
        w = (int(r["start_ns"]) - int(r["submit_ns"])) / NS
        waits.append(w)
        runs.append((int(r["end_ns"]) - int(r["start_ns"])) / NS)

    t_start = min(int(r["submit_ns"]) for r in ok) / NS
    t_end = max(int(r["end_ns"]) for r in ok) / NS
    makespan = t_end - t_start
    gpu_s = sum(int(r["gpus"]) * ((int(r["end_ns"]) - int(r["start_ns"])) / NS) for r in ok)

    print(f"  queue wait s : med {st.median(waits):.2f}  p95 {q(waits,0.95):.2f}  "
          f"max {max(waits):.2f}  min {min(waits):.2f}")
    print(f"  runtime s    : med {st.median(runs):.2f}  (requested burn med "
          f"{st.median([int(r['burn_s']) for r in ok])})")
    print(f"  makespan s   : {makespan:.1f}")
    print(f"  gpu-seconds  : {gpu_s:.0f}")

    # RQ3: gang jobs (width > 1) vs single-GPU jobs.
    for width in sorted({int(r["gpus"]) for r in ok}):
        sel = [r for r in ok if int(r["gpus"]) == width]
        w = [(int(r["start_ns"]) - int(r["submit_ns"])) / NS for r in sel]
        print(f"  width {width}: n={len(sel):>3} wait med {st.median(w):8.2f}  "
              f"p95 {q(w,0.95):8.2f}")

    # RQ4: priority classes.
    prios = sorted({int(r["priority"]) for r in ok})
    if len(prios) > 1:
        for p in prios:
            sel = [r for r in ok if int(r["priority"]) == p]
            w = [(int(r["start_ns"]) - int(r["submit_ns"])) / NS for r in sel]
            print(f"  prio {p}: n={len(sel):>3} wait med {st.median(w):8.2f}  "
                  f"p95 {q(w,0.95):8.2f}")

    # Backfill / reordering evidence: a pair (a,b) is an inversion when a was
    # submitted before b but b started first -- i.e. the scheduler ran a later
    # arrival ahead of an earlier one. n is small, so count all pairs exactly
    # rather than sampling adjacent ones.
    inv = 0
    jumped = {}  # how often each job jumped ahead of an earlier arrival
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            first, second = (a, b) if int(a["submit_ns"]) <= int(b["submit_ns"]) else (b, a)
            if int(second["start_ns"]) < int(first["start_ns"]):
                inv += 1
                jumped[second["id"]] = jumped.get(second["id"], 0) + 1
    pairs = len(ok) * (len(ok) - 1) // 2
    print(f"  backfill inversions: {inv}/{pairs} pairs ({100 * inv / pairs:.1f}%)")
    if jumped:
        top = sorted(jumped.items(), key=lambda kv: -kv[1])[:3]
        byid = {r["id"]: r for r in ok}
        print("  most-backfilled jobs: " + ", ".join(
            f"{jid} (gpus={byid[jid]['gpus']}, jumped {n})" for jid, n in top))
    return {"name": name, "wait_med": st.median(waits), "makespan": makespan,
            "inv_pct": 100 * inv / pairs}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    legs = [leg(p) for p in sys.argv[1:]]
    legs = [x for x in legs if x]
    if len(legs) > 1:
        print("\n### cross-stack comparison")
        base = legs[0]
        for l in legs[1:]:
            print(f"  {l['name']} vs {base['name']}: "
                  f"wait med {l['wait_med']:.2f} vs {base['wait_med']:.2f} "
                  f"({l['wait_med'] / base['wait_med']:.2f}x)  |  "
                  f"makespan {l['makespan']:.1f} vs {base['makespan']:.1f} "
                  f"({l['makespan'] / base['makespan']:.2f}x)  |  "
                  f"backfill {l['inv_pct']:.1f}% vs {base['inv_pct']:.1f}%")


if __name__ == "__main__":
    main()
