#!/usr/bin/env bash
# Paper 6 — common testbed setup. Run FIRST, on the bare VM, as root.
#
# Builds ONE gpu_burn binary that both stacks execute, so the workload is
# byte-identical across Slurm and Kubernetes: Slurm runs it directly on the
# host, K8s runs the same path hostPath-mounted into a CUDA base image.
set -euo pipefail

echo "== host facts (record these in results.md) =="
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv
nproc; free -g | head -2; uname -r
echo

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -yqq git build-essential python3 python3-pip jq curl nvidia-cuda-toolkit

mkdir -p /opt/p6 /var/log/p6
chmod 1777 /var/log/p6   # both stacks write result records here

# gpu_burn: real CUDA SGEMM load, not a sleep sham.
#
# Build for THIS card's compute capability. A default-arch build produces a
# compare kernel the device cannot load, and gpu_burn then exits rc=123 having
# touched no GPU at all -- which still looks like a completed job to both
# schedulers. That failure mode would silently invalidate every measurement,
# so the arch is pinned and the smoke test below verifies actual load.
CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.')
echo "building gpu_burn for compute capability sm_${CC}"
if [ ! -x /opt/p6/gpu_burn ] || [ ! -f /opt/p6/compare.fatbin ]; then
  rm -rf /tmp/gpu-burn
  git clone -q https://github.com/wilicc/gpu-burn /tmp/gpu-burn
  make -C /tmp/gpu-burn COMPUTE="$CC" >/dev/null
  cp -f /tmp/gpu-burn/gpu_burn /opt/p6/
  # This build ships compare.fatbin; older ones ship compare.ptx. Take whichever
  # exists -- gpu_burn looks for it in its working directory.
  cp -f /tmp/gpu-burn/compare.fatbin /opt/p6/ 2>/dev/null || true
  cp -f /tmp/gpu-burn/compare.ptx /opt/p6/ 2>/dev/null || true
fi

# The job wrapper both stacks invoke. Emits ns-resolution timestamps around
# the burn so start/end are measured at the workload, not at the scheduler.
cat > /opt/p6/job.sh <<'JOB'
#!/usr/bin/env bash
# args: JOB_ID BURN_SECONDS STACK
set -uo pipefail
JOB_ID="$1"; BURN="$2"; STACK="$3"
REC="/var/log/p6/${STACK}_${JOB_ID}.json"
START_NS=$(date +%s%N)
# gpu_burn is built on the host against its CUDA libs; container images ship a
# different CUDA major (libcublas.so.11 vs .12) and the binary fails rc=127.
# Ship the host libs with the binary so both stacks execute the same thing.
export LD_LIBRARY_PATH=/opt/p6/lib:${LD_LIBRARY_PATH:-}
cd /opt/p6
./gpu_burn "$BURN" >/dev/null 2>&1
RC=$?
END_NS=$(date +%s%N)
cat > "$REC" <<EOF
{"job_id":"$JOB_ID","stack":"$STACK","start_ns":$START_NS,"end_ns":$END_NS,
 "rc":$RC,"burn_s":$BURN,"node":"$(hostname)",
 "gpus_visible":"${CUDA_VISIBLE_DEVICES:-${NVIDIA_VISIBLE_DEVICES:-all}}"}
EOF
exit $RC
JOB
chmod +x /opt/p6/job.sh

# Bundle the CUDA libraries gpu_burn resolved against, so the identical binary
# runs unmodified inside a container image built on a different CUDA major.
mkdir -p /opt/p6/lib
for L in $(ldd /opt/p6/gpu_burn | awk '/=>/ {print $3}' \
           | grep -E 'libcublas|libcublasLt|libcudart|libnvrtc'); do
  cp -Lu "$L" /opt/p6/lib/
done
echo "bundled CUDA libs: $(ls /opt/p6/lib/ | tr '\n' ' ')"

echo "== smoke test: 15s burn, verifying the GPU is ACTUALLY loaded =="
cd /opt/p6
./gpu_burn 15 > /tmp/smoke_burn.log 2>&1 &
BURN_PID=$!
sleep 8
UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | sort -rn | head -1)
wait $BURN_PID || true
GFLOPS=$(grep -oE '[0-9]+ Gflop/s' /tmp/smoke_burn.log | grep -oE '^[0-9]+' | sort -rn | head -1)
echo "peak GPU util during burn: ${UTIL}%   peak Gflop/s: ${GFLOPS:-0}"
if grep -q "Couldn't init a GPU test" /tmp/smoke_burn.log || [ "${GFLOPS:-0}" -lt 100 ]; then
  echo "FATAL: gpu_burn did not load the GPU -- measurements would be meaningless."
  tail -5 /tmp/smoke_burn.log
  exit 1
fi

/opt/p6/job.sh smoke 5 smoke || true
cat /var/log/p6/smoke_smoke.json
echo "common setup OK"
