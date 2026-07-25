# Paper 6 — "Slurm or Kubernetes for AI Clusters? Scheduling Behavior, Measured"

**Genre:** scheduler comparison (Paper-2 formula on the HPC/AI convergence).
**Venue path:** UCC → IC2E/HPC venues → IEEE Access.
**Why intense/viral:** every AI-infra team is fighting this exact war
(Slurm-on-K8s bridges: Slinky, SUNK, CoreWeave). Comparisons are opinion
pieces; nobody publishes measured scheduling behavior on identical hardware.

## RQs
- RQ1: Scheduling latency — submit → running for single jobs across job
  sizes (1/4/8 CPU "GPU-shaped" jobs with resource fences), idle cluster;
  Slurm (sbatch) vs K8s (Job) vs K8s+Kueue (queueing layer).
- RQ2: Contention behavior — job trace (Poisson arrivals, mixed sizes,
  priorities) replayed identically on both: queue wait distributions,
  utilization, head-of-line blocking, backfill effectiveness (Slurm's
  backfill vs Kueue's fair sharing).
- RQ3: Gang/topology — multi-node jobs (MPI-style 2×2): all-or-nothing
  scheduling behavior, partial-allocation deadlock risk per stack.
- RQ4: Preemption + fairness — priority preemption round-trip time and what
  happens to the preempted job (requeue vs kill) per scheduler.

## Testbed (revised 2026-07-19: REAL GPUs per user directive — no fake-gpu)
Real-GPU host(s) rented on RunPod (account funded, balance ~$12.56).
Preferred: one multi-GPU host (e.g. 2–4× 3090/4090) so Slurm gres=gpu:N and
K8s nvidia-device-plugin schedule REAL devices; gang/backfill/preemption
measured across GPUs on one host (RQ3 becomes multi-GPU gang).
PROBED 2026-07-19 (4 probes, ~$0.30): RunPod containers CANNOT host K8s —
k3s control plane runs, but kubelet dies on bind-mount (no CAP_SYS_ADMIN)
and `unshare -Urm` is seccomp-blocked (no rootless escape). Slurm side WOULD
run. K8s side needs a real VM: options = user's VM-rental site (name+creds
needed), Vast.ai VM-mode, or AWS g4dn once G/VT quota lands (pending).
Probe evidence reusable as a P9-style "container platform capability" note.
Both stacks on the SAME host, reimaged/reset between stacks.
Jobs = real CUDA burn kernels sized to occupy GPUs (no sleep-only shams).

## Cost
Multi-GPU host ~$1–2/hr × ~6–10h ≈ $10–20 (fits RunPod balance; top-up may
be needed for the 4-GPU variant).

## Status
- [x] VM provisioning (vast.ai KVM, 2x A5000) + both stack installers (harness/)
- [x] Trace generator + submit/measure harness (ns-stamped, GPU-util gated)
- [x] Ran all 5 legs: slurm, slurm-preempt, k8s, k8s-preempt, k8s-kueue
      (2026-07-24; data/ + analysis.txt)
- [x] All 4 RQs measured:
      RQ1-3 opposite fairness (Slurm even; K8s default narrow-fast/gang-starve;
      Kueue tempers to mid); RQ4 Slurm REQUEUE completes all + 14x priority vs
      K8s preemption FAILS 36/40 (destructive, no checkpoint)
- [x] results.md written (all legs, threats, provenance)
- [x] 10pp draft COMPILED 2026-07-24 (paper.tex + refs.bib + title-named PDF);
      figs fig_wait_cdf/fig_width/fig_preempt/fig_reorder.pdf (make_figures.py)
      + fig_testbed.png arch diagram (make_arch_diagram.py); 8 tables, 5 figs
- [ ] **USER VALIDATES results.md + paper numbers vs data/*.csv**
- [ ] **USER reads every refs.bib entry (none web-verified)**
- [ ] Repo publish + Zenodo → venue (UCC → IC2E/HPC → Access)

Data: papers/paper6-slurm-vs-k8s/data/ (6 result CSVs incl. preempt_bl8,
4 gpuutil traces, trace.json, analysis.txt, analyze.py, run_k8s.py, kueue_leg.sh).
VM 45648948 DESTROYED. Credit $4.37.
