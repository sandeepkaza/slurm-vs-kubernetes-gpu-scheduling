#!/usr/bin/env bash
# Paper 6 — Kubernetes stack on the SAME host. Run after setup_common.sh,
# and only after the Slurm leg is finished and slurmd is stopped (both stacks
# must never hold the GPUs at once).
#
# k3s + NVIDIA container runtime + device plugin, and optionally Kueue for the
# queueing-layer variant (RQ1/RQ2 compare bare K8s Jobs vs Kueue).
set -euo pipefail

WITH_KUEUE="${1:-}"
NGPU=$(nvidia-smi -L | wc -l)

# Never let the two schedulers contend for the same devices.
systemctl stop slurmd slurmctld 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
if ! command -v nvidia-ctk >/dev/null; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -qq
  apt-get install -yqq nvidia-container-toolkit
fi

if ! command -v k3s >/dev/null; then
  curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo "export KUBECONFIG=$KUBECONFIG" >> /root/.bashrc

# DO NOT run `nvidia-ctk runtime configure` against k3s's containerd template.
# The template does not exist on a fresh install, so nvidia-ctk writes a minimal
# one containing only the nvidia runtime; k3s then uses it verbatim, loses its
# own CNI/sandbox settings, and the node never leaves NotReady
# ("cni plugin not initialized"). Modern k3s detects the NVIDIA container
# runtime by itself and registers the `nvidia` RuntimeClass -- nothing to do.
rm -f /var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
systemctl restart k3s
sleep 25
kubectl wait --for=condition=Ready node --all --timeout=240s
kubectl get runtimeclass nvidia >/dev/null 2>&1 \
  || echo "WARN: no 'nvidia' RuntimeClass -- GPUs will not be visible to pods"

kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.15.0/deployments/static/nvidia-device-plugin.yml
# The plugin itself needs the nvidia runtime to enumerate devices.
kubectl -n kube-system patch daemonset nvidia-device-plugin-daemonset --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/runtimeClassName","value":"nvidia"}]' || true
echo "waiting for nvidia.com/gpu to be advertised..."
for i in $(seq 1 60); do
  ADV=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "")
  [ "${ADV:-0}" != "" ] && [ "${ADV:-0}" != "0" ] && break
  sleep 5
done
echo "allocatable nvidia.com/gpu = ${ADV:-none} (expected $NGPU)"
[ "${ADV:-0}" = "$NGPU" ] || echo "WARN: device plugin advertised ${ADV:-none}, host has $NGPU"

# RQ4: a preemptable low class and a high class, mirroring the Slurm partitions.
kubectl apply -f - <<'EOF'
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata: {name: p6-low}
value: 100
preemptionPolicy: PreemptLowerPriority
globalDefault: false
---
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata: {name: p6-high}
value: 10000
preemptionPolicy: PreemptLowerPriority
globalDefault: false
EOF

if [ "$WITH_KUEUE" = "--kueue" ]; then
  kubectl apply --server-side -f https://github.com/kubernetes-sigs/kueue/releases/download/v0.8.1/manifests.yaml
  kubectl -n kueue-system wait --for=condition=Available deploy/kueue-controller-manager --timeout=300s
  sleep 10
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
  # All-or-nothing admission is the point of the Kueue leg (RQ3).
  resourceGroups:
  - coveredResources: ["nvidia.com/gpu"]
    flavors:
    - name: default
      resources:
      - name: "nvidia.com/gpu"
        nominalQuota: $NGPU
---
apiVersion: kueue.x-k8s.io/v1beta1
kind: LocalQueue
metadata: {name: p6-lq, namespace: default}
spec: {clusterQueue: p6-cq}
EOF
  echo "kueue ready"
fi

echo "== 5s GPU smoke pod =="
kubectl delete job k8ssmoke --ignore-not-found >/dev/null 2>&1
kubectl apply -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata: {name: k8ssmoke}
spec:
  backoffLimit: 0
  template:
    spec:
      runtimeClassName: nvidia
      restartPolicy: Never
      containers:
      - name: burn
        image: nvidia/cuda:12.4.1-runtime-ubuntu22.04
        command: ["/opt/p6/job.sh", "k8ssmoke", "5", "k8s"]
        resources: {limits: {nvidia.com/gpu: 1}}
        volumeMounts:
        - {name: p6, mountPath: /opt/p6}
        - {name: logs, mountPath: /var/log/p6}
      volumes:
      - {name: p6, hostPath: {path: /opt/p6}}
      - {name: logs, hostPath: {path: /var/log/p6}}
EOF
kubectl wait --for=condition=complete job/k8ssmoke --timeout=300s
cat /var/log/p6/k8s_k8ssmoke.json
echo "k8s setup OK"
