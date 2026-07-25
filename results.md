# Paper 6 results — Slurm vs Kubernetes scheduling on one multi-GPU host

**Testbed.** Single vast.ai KVM instance, host 1647 (Iceland), 2× NVIDIA RTX
A5000 (24 GB each), driver 580.95.05, kernel 6.8.0-87, 12 vCPU. Verified real
VM (systemd-detect-virt=kvm, full capabilities, /dev/kmsg present) — a container
host cannot run a kubelet (measured on RunPod and on a vast Docker instance:
no CAP_SYS_ADMIN, bind-mount EPERM, unshare seccomp-blocked). Both stacks run on
the SAME host, one at a time (slurmd stopped before k3s starts).

**Workload.** One trace replayed identically against every stack. 40 jobs,
seed 20260723, Poisson arrivals (rate 0.15/s, span 296 s), GPU widths 1 (70%)
and 2 (30%), burn 15–35 s, 20% high-priority. Offered load 2.31× the host.
Job = `gpu_burn` (real CUDA SGEMM, ~14–17 TFLOP/s/GPU); ONE binary + bundled
CUDA libs run on both stacks, so the workload is byte-identical.

**Measurement.** Two points on both stacks: `submit_ns` (harness, at
sbatch/kubectl apply) and `start_ns`/`end_ns` (the workload's own ns stamps
around the burn). Scheduler self-accounting (sacct, Pod conditions) collected
as corroboration only. Every leg carried a 5 s GPU-utilisation sampler; all
reported legs peaked at 100% and the Slurm baseline held ~97% saturation
(3389 GPU-s over 1751.7 s × 2 GPUs), confirming the GPUs were actually loaded.

**Overhead caveat.** gpu_burn spends ~38 s in fixed calibration before the
timed burn, so per-job "runtime" (med ~60–64 s) is burn + overhead, identical
on both stacks. It inflates makespan equally and does not bias any cross-stack
comparison, but "runtime" here is not pure GPU-burn time.

---

## Headline: the two schedulers make OPPOSITE fairness tradeoffs

Same hardware, same trace, same jobs. Default configs.

| metric | Slurm | K8s (default sched) |
|---|---|---|
| queue wait, median | 717.6 s | 527.7 s |
| queue wait, p95 | 1331.8 s | 1414.0 s |
| makespan | 1751.7 s | 1716.9 s |
| **width-1 wait, median** | **706.8 s** | **189.4 s** |
| **width-2 gang wait, median** | **728.5 s** | **1182.2 s** |
| width-1 wait, p95 | 984.4 s | 651.3 s |
| width-2 gang wait, p95 | 1385.2 s | 1485.9 s |
| backfill inversions | 49/780 (6.3%) | 312/780 (40.0%) |

**Finding (RQ2+RQ3).** Slurm's backfill is conservative: single-GPU and 2-GPU
gang jobs wait almost the same (706.8 vs 728.5 s median), and it reorders the
submit sequence only 6.3% of the time. Kubernetes' default scheduler is
aggressively greedy: it lets narrow jobs jump ahead 40% of the time, so
single-GPU jobs clear **3.7× faster** than under Slurm (189.4 vs 706.8 s) — but
2-GPU gang jobs **starve**, waiting 1.6× longer than under Slurm (1182.2 vs
728.5 s) and worse than K8s's own single-GPU jobs by 6.2×. K8s optimises
narrow-job throughput at the cost of gang-job starvation; Slurm spreads the
wait evenly. The gang cost under Slurm shows only in the tail (p95 1385 vs
984 s); under K8s it dominates the median.

Makespans are within 2%, so neither ordering wins on total throughput — the
difference is entirely in *who waits*.

---

## RQ4 — priority preemption

Same trace, high-priority jobs routed to Slurm's `high` partition
(PreemptMode=REQUEUE) / K8s `p6-high` PriorityClass.

| stack | outcome |
|---|---|
| Slurm, no preempt (baseline) | all 40 complete; prio-1 742.8 s ≈ prio-0 717.6 s (label alone does nothing) |
| Slurm, preempt | **all 40 complete**; prio-1 med **49.3 s** vs prio-0 690.0 s (**14×**) |
| K8s, preempt (backoffLimit 8) | **only 4/40 complete**: 0/32 low-prio, 4/8 high-prio; 36 jobs FAILED |

**Finding (RQ4) — the schedulers differ in KIND, not degree.** Slurm's
`PreemptMode=REQUEUE` returns a preempted job to the queue: every one of the 40
jobs completes, and routing high-priority work to the `high` partition clears
it in 49.3 s median (p95 75.3 s) against 690.0 s for the normal class — a 14×
priority speedup at near-zero cost to the normal class, because the victim
waits rather than dies.

Kubernetes default-scheduler priority preemption is *destructive* on the same
trace. A preempted pod is killed, and the Job controller restarts it from
scratch — there is no checkpoint, so the ~40 s of gpu_burn calibration is lost
on every eviction. Under sustained contention the restarts exhaust the Job's
`backoffLimit` and the job **fails** rather than eventually running. With
`backoffLimit=8` (K8s default is 6), **all 32 low-priority jobs failed and 4 of
8 high-priority jobs failed; only 4 jobs completed.** The "K8s preemption wait"
is therefore not measurable as a latency — the jobs do not survive to report
one, which is itself the result: naive priority preemption on non-checkpointed
GPU batch is a correctness hazard, not a latency knob. Slurm's requeue model is
the reason the same workload completes.

Caveat: the failure count depends on `backoffLimit` and on the job's
restart cost (checkpointed jobs would survive preemption); the *direction* —
K8s kills-and-restarts vs Slurm requeues — is a scheduler-model difference, not
a tuning artifact. To preempt safely on K8s the workload must checkpoint or use
a gang/queue layer that admits all-or-nothing (see RQ3, Kueue).

---

## RQ3 — gang / all-or-nothing admission (Kueue)

Same trace, bare K8s Jobs (default scheduler) vs Kueue's suspend-based
all-or-nothing admission, against the Slurm reference.

| metric | Slurm | K8s default | K8s + Kueue |
|---|---|---|---|
| width-1 wait, median | 706.8 s | 189.4 s | 308.4 s |
| width-2 gang wait, median | 728.5 s | 1182.2 s | 1081.4 s |
| width-1 wait, p95 | 984.4 s | 651.3 s | 1122.7 s |
| width-2 gang wait, p95 | 1385.2 s | 1485.9 s | 1302.9 s |
| backfill inversions | 6.3% | 40.0% | 16.5% |
| makespan | 1751.7 s | 1716.9 s | 1728.6 s |

**Finding (RQ3).** Adding Kueue's queue layer moves the K8s default scheduler
back toward Slurm-like fairness. Backfill inversions fall from 40.0% to 16.5%
(Slurm is 6.3%): Kueue admits work in queue order instead of letting narrow
jobs greedily jump ahead. The gang-starvation the default scheduler causes is
partly relieved — width-2 median wait drops from 1182 s to 1081 s and gang p95
improves from 1486 s to 1303 s — at the expected cost to narrow jobs (median
189 s → 308 s, still faster than Slurm's 707 s). Makespan is unchanged (within
1%), so Kueue reorders who waits without costing throughput. Kueue sits between
the greedy default scheduler and Slurm's conservative backfill: it does not
fully close the gang-fairness gap, but it removes the worst of the default
scheduler's narrow-job bias.

---

## Per-leg detail

### Slurm (baseline)
- queue wait: med 717.6, p95 1331.8, max 1417.7, min 0.78 s
- makespan 1751.7 s; 3389 GPU-s (~97% of 2-GPU capacity)
- width 1 (n=27): med 706.8, p95 984.4 | width 2 (n=13): med 728.5, p95 1385.2
- backfill inversions 6.3%

### K8s (default scheduler)
- queue wait: med 527.7, p95 1414.0, max 1520.7, min 2.15 s
- makespan 1716.9 s; 3164 GPU-s
- width 1 (n=27): med 189.4, p95 651.3 | width 2 (n=13): med 1182.2, p95 1485.9
- backfill inversions 40.0%

### Slurm (preempt)
- queue wait: med 612.8, p95 1368.7, max 1710.9 s; makespan 1810.0 s
- prio 0 (n=32): med 690.0 | prio 1 (n=8): med 49.3, p95 75.3
- backfill inversions 36.3%

### K8s (preempt, backoffLimit 8)
- 4/40 jobs completed (0/32 low-prio, 4/8 high-prio); 36 FAILED
- survivors' wait med 3.2 s (the high-prio jobs that ran without repeated eviction)
- see RQ4 — the failure count, not a latency, is the result

### K8s + Kueue
- queue wait: med 424.6, p95 1337.4, max 1394.3 s; makespan 1728.6 s
- width 1 (n=27): med 308.4, p95 1122.7 | width 2 (n=13): med 1081.4, p95 1302.9
- backfill inversions 16.5%

---

## Threats / scope
- **2-GPU single host**, so RQ3's gang is 2-wide; the planned 2×2 multi-node
  topology is out of scope on this hardware. Gang admission and partial-alloc
  behaviour are still observable, but cross-node gang is not.
- Single trace, single seed; prevalence not trend. Trace + seed published.
- gpu_burn calibration overhead (~38 s) dominates short-burn runtime; identical
  both stacks (does not bias comparison) but makespan is inflated vs a
  zero-overhead job.
- Consumer A5000s, not datacenter GPUs; scheduling behaviour is card-agnostic
  but absolute burn throughput is not.
- checkov/sacct-style scheduler self-timings corroborate but are not the
  headline source; all headline numbers derive from harness + workload ns
  stamps.
- **Reproducibility / provenance.** All five legs were collected on one VM in
  one session; the K8s preempt leg was rerun after two harness bugs (missing
  PriorityClasses; a preempted-pod termination-grace livelock — fixed with
  `terminationGracePeriodSeconds: 0` to match Slurm's near-instant REQUEUE) and
  a stale-timestamp mix that produced physically impossible waits (caught by a
  sanity guard: a job whose start precedes its submit, or waits longer than the
  leg could run, is dropped). The Kueue leg required patching the upstream
  controller's removed `kube-rbac-proxy` sidecar image, and a mid-run host
  NVIDIA driver auto-upgrade (kernel/userspace NVML mismatch) forced a VM reboot
  (unattended-upgrades then disabled). Every reported leg carries a GPU-util
  trace (`gpuutil_*.csv`) showing sustained 100% peak / ~97% saturation, so a
  leg that failed to load the GPUs cannot masquerade as a valid measurement.
- K8s preempt failure count is `backoffLimit`-dependent (8 here; K8s default 6)
  and reflects non-checkpointed jobs; the kill-vs-requeue model difference is
  not tuning-dependent.

_All numbers regenerated by `harness/analyze.py` from `results_*.csv`.
GPU-utilisation traces in `gpuutil_*.csv`. USER VALIDATES before any draft._
