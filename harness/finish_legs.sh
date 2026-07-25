#!/usr/bin/env bash
# Paper 6 — re-run the clean k8s_preempt leg, then the kueue leg, then analyse.
# Assumes k3s up, nvidia.com/gpu=2 advertised, p6-low/p6-high present.
set -uo pipefail
cd /root
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
exec >>/root/finish_legs.log 2>&1
say() { echo "[$(date -u +%H:%M:%S)] $*"; }

sample_gpu() {
  local out="/root/gpuutil_$1.csv"; echo "ts_ns,gpu,util,mem_mib" > "$out"
  while :; do
    nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader,nounits \
      | while IFS=, read -r i u m; do echo "$(date +%s%N),$i,$(echo $u|tr -d ' '),$(echo $m|tr -d ' ')" >> "$out"; done
    sleep 5
  done
}
run_leg() {
  local tag="$1"; shift; say "LEG START: $tag :: $*"
  sample_gpu "$tag" & local SP=$!
  "$@"; local rc=$?; kill $SP 2>/dev/null
  local peak; peak=$(awk -F, 'NR>1 && $3+0>m {m=$3+0} END{print m+0}' "/root/gpuutil_$tag.csv")
  say "LEG END: $tag rc=$rc peak_gpu_util=${peak}%"
  [ "${peak:-0}" -lt 50 ] && { say "FATAL: $tag never loaded GPUs"; return 1; }
  return 0
}

# preconditions
for r in p6-low p6-high; do
  kubectl get priorityclass $r >/dev/null 2>&1 || { say "FATAL: missing PriorityClass $r"; exit 1; }
done
ADV=$(kubectl get node ubuntu -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null)
[ "${ADV:-0}" = "2" ] || { say "FATAL: nvidia.com/gpu=${ADV:-none}"; exit 1; }

run_leg k8s_preempt python3 run_k8s.py --trace trace.json --preempt --out results_k8s_preempt.csv || exit 1

say "installing kueue ..."
bash setup_k8s.sh --kueue > /root/setup_kueue.log 2>&1
kubectl get clusterqueue p6-cq >/dev/null 2>&1 || { say "FATAL: kueue ClusterQueue missing"; tail -15 /root/setup_kueue.log; exit 1; }
run_leg k8s_kueue python3 run_k8s.py --trace trace.json --kueue --out results_k8s_kueue.csv || exit 1

say "ALL LEGS DONE"
python3 analyze.py results_slurm.csv results_slurm_preempt.csv results_k8s.csv \
  results_k8s_preempt.csv results_k8s_kueue.csv > /root/analysis.txt 2>&1
say "analysis written"
