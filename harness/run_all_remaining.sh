#!/usr/bin/env bash
# Paper 6 — drive the remaining legs unattended, with gates.
#
# Assumes setup_common.sh + setup_slurm.sh have run and the first Slurm leg
# is either finished or in flight. Each leg gets a background GPU-utilisation
# sampler, so every result set carries evidence its GPUs were actually loaded
# -- the failure mode that silently invalidated earlier runs was a job that
# "completed" without touching a GPU.
set -uo pipefail
cd /root
LOG=/root/run_all.log
exec >>"$LOG" 2>&1

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

sample_gpu() {  # $1 = tag
  local out="/root/gpuutil_$1.csv"
  echo "ts_ns,gpu,util,mem_mib" > "$out"
  while :; do
    nvidia-smi --query-gpu=index,utilization.gpu,memory.used \
      --format=csv,noheader,nounits | while IFS=, read -r i u m; do
        echo "$(date +%s%N),$i,$(echo $u|tr -d ' '),$(echo $m|tr -d ' ')" >> "$out"
      done
    sleep 5
  done
}

run_leg() {  # $1 = tag, rest = command
  local tag="$1"; shift
  say "LEG START: $tag :: $*"
  sample_gpu "$tag" & local SP=$!
  "$@"; local rc=$?
  kill $SP 2>/dev/null
  # Gate: a leg whose GPUs never got busy is not a valid measurement.
  local peak
  peak=$(awk -F, 'NR>1 && $3+0>m {m=$3+0} END{print m+0}' "/root/gpuutil_$tag.csv")
  say "LEG END: $tag rc=$rc peak_gpu_util=${peak}%"
  if [ "${peak:-0}" -lt 50 ]; then
    say "FATAL: $tag never loaded the GPUs (peak ${peak}%) -- stopping."
    return 1
  fi
  return $rc
}

# 0. wait for the in-flight Slurm leg
say "waiting for results_slurm.csv ..."
for i in $(seq 1 120); do
  [ -f /root/results_slurm.csv ] && break
  sleep 30
done
[ -f /root/results_slurm.csv ] || { say "FATAL: slurm leg never produced a CSV"; exit 1; }
say "results_slurm.csv present ($(wc -l < /root/results_slurm.csv) lines)"

# 1. Slurm preemption leg (RQ4)
run_leg slurm_preempt python3 run_slurm.py --trace trace.json --preempt \
  --out results_slurm_preempt.csv || exit 1

# 2. Kubernetes stack (stops slurmd; both stacks must never hold GPUs at once)
say "installing k3s ..."
bash setup_k8s.sh > /root/setup_k8s.log 2>&1
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
ADV=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null)
say "k8s advertises nvidia.com/gpu = ${ADV:-none}"
if [ "${ADV:-0}" != "2" ]; then
  say "FATAL: device plugin did not advertise 2 GPUs -- stopping before wasting legs."
  tail -20 /root/setup_k8s.log
  exit 1
fi

run_leg k8s python3 run_k8s.py --trace trace.json --out results_k8s.csv || exit 1
run_leg k8s_preempt python3 run_k8s.py --trace trace.json --preempt \
  --out results_k8s_preempt.csv || exit 1

# 3. Kueue variant (RQ3 all-or-nothing admission)
say "installing kueue ..."
bash setup_k8s.sh --kueue > /root/setup_kueue.log 2>&1
kubectl get clusterqueue p6-cq >/dev/null 2>&1 || { say "FATAL: kueue ClusterQueue missing"; exit 1; }
run_leg k8s_kueue python3 run_k8s.py --trace trace.json --kueue \
  --out results_k8s_kueue.csv || exit 1

say "ALL LEGS DONE"
python3 analyze.py results_slurm.csv results_slurm_preempt.csv results_k8s.csv \
  results_k8s_preempt.csv results_k8s_kueue.csv > /root/analysis.txt 2>&1
say "analysis written"
