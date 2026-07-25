# Paper 6 harness — Slurm vs Kubernetes on one multi-GPU host

Regenerated 2026-07-23 (the original lived in a session scratchpad and was lost
when that scratchpad was cleaned up — this copy lives in the paper folder so it
survives).

## Design invariant

Both stacks run **the same trace**, **the same binary**, on **the same GPUs**,
and are measured at **the same two points**:

- `submit_ns` — recorded by the harness immediately before `sbatch` / `kubectl apply`
- `start_ns` / `end_ns` — recorded by `/opt/p6/job.sh` around the CUDA burn itself

Scheduler self-accounting (`sacct`, Pod conditions) is collected too, but only
as corroboration; the headline numbers never depend on one scheduler's clock
semantics. `gpu_burn` is compiled once into `/opt/p6/` and hostPath-mounted into
the K8s pods, so the workload is byte-identical across stacks.

The two stacks must never hold the GPUs at the same time — `setup_k8s.sh` stops
`slurmd`/`slurmctld` before it starts.

## Order of operations

```bash
# on the VM, as root
bash setup_common.sh                 # driver facts, gpu_burn, job.sh, smoke test
bash setup_slurm.sh                  # gres=gpu:N, backfill, preempt partitions

python3 trace_gen.py --gpus $(nvidia-smi -L | wc -l) --jobs 60 -o trace.json
python3 run_slurm.py --trace trace.json --out results_slurm.csv
python3 run_slurm.py --trace trace.json --preempt --out results_slurm_preempt.csv

bash setup_k8s.sh                    # k3s + device plugin (stops slurm first)
python3 run_k8s.py --trace trace.json --out results_k8s.csv
python3 run_k8s.py --trace trace.json --preempt --out results_k8s_preempt.csv

bash setup_k8s.sh --kueue            # add the queueing layer
python3 run_k8s.py --trace trace.json --kueue --out results_k8s_kueue.csv

python3 analyze.py results_*.csv
```

Copy the CSVs off the host **before** destroying the instance.

## RQ mapping

| RQ | What produces it |
|---|---|
| RQ1 scheduling latency | `queue wait` on an idle cluster (first jobs of each trace) |
| RQ2 contention | full-trace wait distributions, makespan, order inversions (backfill evidence) |
| RQ3 gang | per-width wait breakdown; Kueue leg gates admission all-or-nothing via `suspend: true` |
| RQ4 preemption | `--preempt` legs: Slurm `high` partition + `PreemptMode=REQUEUE` vs K8s `p6-high` PriorityClass |

## Self-check

`python3 trace_gen.py --selfcheck` — asserts trace reproducibility under a fixed
seed, monotonic arrivals, widths capped at host GPU count, and that the trace
actually oversubscribes the host (otherwise there is no queue to measure).

## Platform note (why a VM and not a container)

k3s needs a kubelet, and kubelet needs privileges container GPU hosts withhold.
Measured on RunPod (2026-07-19): `/dev/kmsg` missing (bypassable via
`KubeletInUserNamespace`), cgroup-v1 host check (bypassable via
`--fail-cgroupv1=false`), then bind-mount of `/var/lib/kubelet` fails `EPERM`
without `CAP_SYS_ADMIN`, and `unshare -Urm` is seccomp-blocked so there is no
rootless escape. RunPod has since removed its Kata-based pods, which were the
only nested-containerization path there. Hence a real VM — or a container host
that grants `CAP_SYS_ADMIN`, which is what the vast.ai Docker-instance probe
tests.
