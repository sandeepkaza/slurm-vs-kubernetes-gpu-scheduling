#!/usr/bin/env bash
# Paper 6 — install Kueue and run the kueue leg directly (k3s already up with
# nvidia.com/gpu=2). Avoids setup_k8s.sh's full k3s restart.
set -uo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
cd /root
say() { echo "[$(date -u +%H:%M:%S)] $*"; }

say "installing kueue v0.8.1 ..."
kubectl apply --server-side -f https://github.com/kubernetes-sigs/kueue/releases/download/v0.8.1/manifests.yaml
kubectl -n kueue-system wait --for=condition=Available deploy/kueue-controller-manager --timeout=300s
sleep 15

kubectl apply -f - <<EOF
apiVersion: kueue.x-k8s.io/v1beta1
kind: ResourceFlavor
metadata: {name: default}
---
apiVersion: kueue.x-k8s.io/v1beta1
kind: ClusterQueue
metadata: {name: p6-cq}
spec:
  namespaceSelector: {}
  resourceGroups:
  - coveredResources: ["nvidia.com/gpu"]
    flavors:
    - name: default
      resources:
      - name: "nvidia.com/gpu"
        nominalQuota: 2
---
apiVersion: kueue.x-k8s.io/v1beta1
kind: LocalQueue
metadata: {name: p6-lq, namespace: default}
spec: {clusterQueue: p6-cq}
EOF
sleep 8
kubectl get clusterqueue p6-cq || { say "FATAL: ClusterQueue missing"; exit 1; }
say "kueue ready"

# GPU-util sampler + leg
echo "ts_ns,gpu,util,mem_mib" > /root/gpuutil_k8s_kueue.csv
( while :; do nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader,nounits \
    | while IFS=, read -r i u m; do echo "$(date +%s%N),$i,$(echo $u|tr -d ' '),$(echo $m|tr -d ' ')" >> /root/gpuutil_k8s_kueue.csv; done
  sleep 5; done ) & SP=$!
say "LEG START k8s_kueue"
python3 run_k8s.py --trace trace.json --kueue --out results_k8s_kueue.csv
kill $SP 2>/dev/null
peak=$(awk -F, 'NR>1 && $3+0>m {m=$3+0} END{print m+0}' /root/gpuutil_k8s_kueue.csv)
say "LEG END k8s_kueue peak_gpu_util=${peak}%"

say "final analysis"
python3 analyze.py results_slurm.csv results_slurm_preempt.csv results_k8s.csv \
  results_k8s_preempt.csv results_k8s_kueue.csv > /root/analysis.txt 2>&1
say "analysis written"
