"""Paper 6 — replay the trace against Slurm.

Submits each job at its trace arrival time via sbatch, then polls sacct/squeue
for submit/start/end. Queue wait = start - submit, measured from Slurm's own
accounting plus the ns timestamps job.sh writes at the workload itself.

Usage:
  python3 run_slurm.py --trace trace.json --out results_slurm.csv
  python3 run_slurm.py --trace trace.json --preempt   # RQ4 leg
"""

import argparse
import csv
import json
import os
import subprocess
import time
from pathlib import Path

LOGDIR = Path("/var/log/p6")


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def submit(job, preempt):
    """sbatch one trace job; return (slurm_id, submit_ns)."""
    part = "high" if (preempt and job["priority"] == 1) else "normal"
    script = (
        "#!/bin/bash\n"
        f"#SBATCH --job-name={job['id']}\n"
        f"#SBATCH --partition={part}\n"
        f"#SBATCH --gres=gpu:{job['gpus']}\n"
        f"#SBATCH --output=/var/log/p6/slurm_{job['id']}.out\n"
        # Gang semantics: one step holding all requested GPUs at once (RQ3).
        f"srun --gres=gpu:{job['gpus']} /opt/p6/job.sh {job['id']} {job['burn_s']} slurm\n"
    )
    path = f"/tmp/{job['id']}.sbatch"
    Path(path).write_text(script)
    submit_ns = time.time_ns()
    r = sh(f"sbatch --parsable {path}")
    if r.returncode != 0:
        print(f"  !! sbatch failed for {job['id']}: {r.stderr.strip()}")
        return None, submit_ns
    return r.stdout.strip().split(";")[0], submit_ns


def collect(rows):
    """Scheduler-side timings, best effort.

    There is no slurmdbd on this testbed, so sacct usually returns nothing;
    jobcomp/filetxt is the fallback. Either way these fields are corroboration
    only -- every headline number comes from submit_ns and the job.sh stamps.
    """
    acct = {}
    ids = ",".join(r["slurm_id"] for r in rows if r["slurm_id"])
    if ids:
        fmt = "JobID,JobName,Submit,Start,End,State,Priority,AllocTRES"
        r = sh(f"sacct -j {ids} --format={fmt} -P -n --allocations")
        for line in r.stdout.strip().splitlines():
            f = line.split("|")
            if len(f) >= 6:
                acct[f[0]] = {"submit": f[2], "start": f[3], "end": f[4],
                              "state": f[5], "alloc": f[7] if len(f) > 7 else ""}

    # jobcomp/filetxt: "JobId=123 ... StartTime=... EndTime=... JobState=..."
    try:
        for line in Path("/var/log/slurm/jobcomp.log").read_text().splitlines():
            kv = dict(p.split("=", 1) for p in line.split() if "=" in p)
            jid = kv.get("JobId")
            if jid and jid not in acct:
                acct[jid] = {"submit": kv.get("SubmitTime", ""),
                             "start": kv.get("StartTime", ""),
                             "end": kv.get("EndTime", ""),
                             "state": kv.get("JobState", ""),
                             "alloc": kv.get("GRES", "")}
    except FileNotFoundError:
        pass
    return acct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", default="trace.json")
    ap.add_argument("--out", default="results_slurm.csv")
    ap.add_argument("--preempt", action="store_true",
                    help="RQ4: route priority-1 jobs to the high partition")
    a = ap.parse_args()

    trace = json.loads(Path(a.trace).read_text())
    jobs = trace["jobs"]
    print(f"replaying {len(jobs)} jobs (preempt={a.preempt}); "
          f"offered load {trace['meta']['offered_load']:.2f}")

    for f in LOGDIR.glob("slurm_*"):
        f.unlink()

    rows = []
    t0 = time.time()
    for j in jobs:
        # Hold the trace's arrival schedule; never submit early.
        wait = j["arrival_s"] - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)
        sid, sub_ns = submit(j, a.preempt)
        rows.append({**j, "slurm_id": sid, "submit_ns": sub_ns})
        print(f"  submitted {j['id']} gpus={j['gpus']} prio={j['priority']} -> {sid}")

    print("all submitted; draining...")
    while True:
        q = sh("squeue -h -o %i").stdout.strip()
        if not q:
            break
        print(f"  {len(q.splitlines())} still queued/running")
        time.sleep(15)

    acct = collect(rows)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "id", "gpus", "burn_s", "priority", "arrival_s", "slurm_id",
            "submit_ns", "start_ns", "end_ns", "sacct_submit", "sacct_start",
            "sacct_end", "state", "alloc"])
        w.writeheader()
        for r in rows:
            rec = {}
            p = LOGDIR / f"slurm_{r['id']}.json"
            if p.exists():
                rec = json.loads(p.read_text())
            ac = acct.get(r["slurm_id"] or "", {})
            w.writerow({
                "id": r["id"], "gpus": r["gpus"], "burn_s": r["burn_s"],
                "priority": r["priority"], "arrival_s": r["arrival_s"],
                "slurm_id": r["slurm_id"], "submit_ns": r["submit_ns"],
                "start_ns": rec.get("start_ns", ""), "end_ns": rec.get("end_ns", ""),
                "sacct_submit": ac.get("submit", ""), "sacct_start": ac.get("start", ""),
                "sacct_end": ac.get("end", ""), "state": ac.get("state", ""),
                "alloc": ac.get("alloc", ""),
            })
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
