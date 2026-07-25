"""Paper 6 — replay the same trace against Kubernetes (bare Jobs or Kueue).

Same arrival schedule, same job.sh, same GPUs as the Slurm leg. Queue wait is
taken from the Pod's scheduled/started conditions plus the ns timestamps the
workload writes, so both stacks are measured at the same two points.

Usage:
  python3 run_k8s.py --trace trace.json --out results_k8s.csv
  python3 run_k8s.py --trace trace.json --kueue --out results_k8s_kueue.csv
  python3 run_k8s.py --trace trace.json --preempt --out results_k8s_preempt.csv
"""

import argparse
import csv
import json
import subprocess
import time
from pathlib import Path

LOGDIR = Path("/var/log/p6")
# -runtime, not -base: gpu_burn links libcublas/libcublasLt, which -base omits.
# The exact versions still come from /opt/p6/lib (see setup_common.sh), so the
# container and the Slurm host run one identical binary against one identical
# library set -- the image only has to supply a working loader.
IMAGE = "nvidia/cuda:12.4.1-runtime-ubuntu22.04"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def manifest(job, kueue, preempt):
    labels = ""
    if kueue:
        labels = '\n    kueue.x-k8s.io/queue-name: p6-lq'
    prio = ""
    if preempt:
        prio = f'\n      priorityClassName: p6-{"high" if job["priority"] == 1 else "low"}'
    # suspend:true is required for Kueue to gate admission; Kueue unsuspends
    # only when the whole GPU request fits (the all-or-nothing behaviour in RQ3).
    suspend = "\n  suspend: true" if kueue else ""
    # In the preempt leg K8s evicts (kills) a lower-priority pod. To mirror
    # Slurm's PreemptMode=REQUEUE -- where the victim returns to the queue and
    # runs later -- the Job must be allowed to recreate its pod, so a preempted
    # job eventually completes rather than failing terminally. backoffLimit 0
    # elsewhere keeps a genuinely broken job from looping. Queue wait is then
    # submit -> the SUCCESSFUL pod's start, which is exactly the requeue wait.
    backoff = 8 if preempt else 0
    return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: {job['id']}
  labels:
    p6: "true"{labels}
spec:
  backoffLimit: {backoff}
  completions: 1
  parallelism: 1{suspend}
  template:
    metadata:
      labels: {{p6: "true"}}
    spec:
      # k3s auto-detects the NVIDIA container runtime and registers this class;
      # without it the pod gets the default runtime and cannot see the GPUs.
      runtimeClassName: nvidia
      # A preempted pod holds its GPU for the whole termination grace, during
      # which the device plugin cannot reallocate it -- with the 30s default
      # this idles a GPU on every eviction and livelocks the preempt leg. Slurm
      # REQUEUE frees the resource near-instantly, so grace 0 is what makes the
      # two preemption paths comparable (we measure scheduler round-trip, not
      # pod shutdown). Non-preempt legs never hit this.
      terminationGracePeriodSeconds: {0 if preempt else 30}
      restartPolicy: Never{prio}
      containers:
      - name: burn
        image: {IMAGE}
        command: ["/opt/p6/job.sh", "{job['id']}", "{job['burn_s']}", "k8s"]
        resources:
          limits:
            nvidia.com/gpu: {job['gpus']}
        volumeMounts:
        - {{name: p6, mountPath: /opt/p6}}
        - {{name: logs, mountPath: /var/log/p6}}
      volumes:
      - {{name: p6, hostPath: {{path: /opt/p6}}}}
      - {{name: logs, hostPath: {{path: /var/log/p6}}}}
"""


def pod_times(job_id):
    """Scheduling times of the pod that actually ran the job.

    With backoffLimit>0 a preempted job leaves several pods (failed attempts +
    one success). Pick the Succeeded pod if there is one, else the newest --
    the succeeded pod's start is the requeue-adjusted start we want.
    """
    r = sh(f"kubectl get pods -l job-name={job_id} "
           "-o jsonpath='{range .items[*]}{.status.phase}|{.metadata.creationTimestamp}|"
           "{.status.startTime}|{.metadata.name};{end}'")
    cands = [p.split("|") for p in r.stdout.strip().strip(";").split(";") if "|" in p]
    if not cands:
        return {"pod_created": "", "pod_scheduled": "", "pod_start": "", "phase": ""}
    succ = [c for c in cands if c[0] == "Succeeded"]
    chosen = succ[-1] if succ else cands[-1]
    phase, created, start = chosen[0], chosen[1], chosen[2]
    return {"pod_created": created, "pod_scheduled": "", "pod_start": start,
            "phase": phase, "pod_attempts": len(cands)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", default="trace.json")
    ap.add_argument("--out", default="results_k8s.csv")
    ap.add_argument("--kueue", action="store_true")
    ap.add_argument("--preempt", action="store_true")
    a = ap.parse_args()

    trace = json.loads(Path(a.trace).read_text())
    jobs = trace["jobs"]
    print(f"replaying {len(jobs)} jobs (kueue={a.kueue} preempt={a.preempt})")

    sh("kubectl delete jobs -l p6=true --ignore-not-found")
    for f in LOGDIR.glob("k8s_*"):
        f.unlink()
    time.sleep(5)

    rows = []
    t0 = time.time()
    for j in jobs:
        wait = j["arrival_s"] - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)
        Path(f"/tmp/{j['id']}.yaml").write_text(manifest(j, a.kueue, a.preempt))
        submit_ns = time.time_ns()
        r = sh(f"kubectl apply -f /tmp/{j['id']}.yaml")
        if r.returncode != 0:
            print(f"  !! apply failed {j['id']}: {r.stderr.strip()[:120]}")
        rows.append({**j, "submit_ns": submit_ns})
        print(f"  submitted {j['id']} gpus={j['gpus']} prio={j['priority']}")

    print("all submitted; draining...")
    # A job is terminal when it succeeds OR exhausts its backoffLimit (Failed).
    # Counting only successes would hang forever the first time a preempted job
    # runs out of retries; a wall-clock cap past the last arrival is the final
    # backstop so a wedged pod cannot block the whole leg.
    deadline = time.time() + trace["meta"]["span_s"] + 1800
    while True:
        succ = sh("kubectl get jobs -l p6=true "
                  "-o jsonpath='{range .items[*]}{.metadata.name}={.status.succeeded},{.status.failed};{end}'")
        complete = failed = 0
        for tok in succ.stdout.strip().strip(";").split(";"):
            if "=" not in tok:
                continue
            _, sf = tok.split("=", 1)
            s, fl = (sf.split(",") + ["", ""])[:2]
            if s.strip() not in ("", "0"):
                complete += 1
            elif fl.strip() not in ("", "0"):
                failed += 1
        terminal = complete + failed
        if terminal >= len(jobs):
            print(f"  drained: {complete} succeeded, {failed} failed")
            break
        if time.time() > deadline:
            print(f"  DEADLINE hit: {complete} succeeded, {failed} failed, "
                  f"{len(jobs) - terminal} still active -- stopping drain")
            break
        print(f"  {complete} complete, {failed} failed, {len(jobs) - terminal} active")
        time.sleep(15)

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "id", "gpus", "burn_s", "priority", "arrival_s", "submit_ns",
            "start_ns", "end_ns", "pod_created", "pod_scheduled", "pod_start",
            "phase", "pod_attempts"])
        w.writeheader()
        for r in rows:
            rec = {}
            p = LOGDIR / f"k8s_{r['id']}.json"
            if p.exists():
                rec = json.loads(p.read_text())
            t = pod_times(r["id"])
            start_ns, end_ns = rec.get("start_ns", ""), rec.get("end_ns", "")
            # Sanity guard: a start before submit, or a wait longer than the
            # whole leg could possibly be, means the record is stale (a leftover
            # k8s_<id>.json from an earlier run that the wipe missed). Blank the
            # timings so analyze.py counts the row incomplete instead of
            # emitting a plausible-but-wrong number. (Silent stale-timing
            # corruption bit this leg once already.)
            if start_ns:
                dwait = (int(start_ns) - int(r["submit_ns"])) / 1e9
                if dwait < -1 or dwait > trace["meta"]["span_s"] + 7200:
                    print(f"  !! {r['id']}: start-submit={dwait:.0f}s out of range, "
                          f"blanking stale timing")
                    start_ns = end_ns = ""
            w.writerow({
                "id": r["id"], "gpus": r["gpus"], "burn_s": r["burn_s"],
                "priority": r["priority"], "arrival_s": r["arrival_s"],
                "submit_ns": r["submit_ns"], "start_ns": start_ns,
                "end_ns": end_ns, **t})
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
