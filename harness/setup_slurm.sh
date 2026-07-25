#!/usr/bin/env bash
# Paper 6 — Slurm stack on a single multi-GPU host. Run after setup_common.sh.
# Configures gres=gpu:N, the backfill scheduler (RQ2), and a preemptable
# low-priority partition (RQ4).
set -euo pipefail

NGPU=$(nvidia-smi -L | wc -l)
CPUS=$(nproc)
MEMMB=$(( $(free -m | awk '/^Mem:/{print $2}') * 9 / 10 ))
HOST=$(hostname)
echo "configuring slurm for $NGPU GPUs / $CPUS cpus on $HOST"

export DEBIAN_FRONTEND=noninteractive
apt-get install -yqq slurm-wlm slurm-wlm-basic-plugins munge

# munge key must exist before slurmctld/slurmd start.
if [ ! -s /etc/munge/munge.key ]; then
  /usr/sbin/create-munge-key -f
fi
chown munge:munge /etc/munge/munge.key && chmod 400 /etc/munge/munge.key
systemctl enable --now munge

cat > /etc/slurm/gres.conf <<EOF
AutoDetect=off
$(for i in $(seq 0 $((NGPU-1))); do echo "Name=gpu File=/dev/nvidia$i"; done)
EOF

cat > /etc/slurm/slurm.conf <<EOF
ClusterName=p6
SlurmctldHost=$HOST
SlurmUser=root
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd
ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
ReturnToService=2

# --- RQ2: backfill is the behaviour under test, tuned to react fast ---
SchedulerType=sched/backfill
SchedulerParameters=bf_interval=5,bf_resolution=5,bf_window=1440,bf_max_job_test=500,defer
SelectType=select/cons_tres
SelectTypeParameters=CR_CPU_Memory
# CRITICAL: with CR_*_Memory and no default, every job is granted the node's
# ENTIRE memory, so a second job can never be co-scheduled and a multi-GPU node
# silently degrades to a serial one-GPU queue (observed: AllocMem == RealMemory
# for a single gres:gpu:1 job, second GPU permanently idle, later jobs pending
# on "Resources"). Bound per-CPU memory so GPUs, not RAM, are the binding
# constraint -- which is the whole point of the testbed.
DefMemPerCPU=4096
MaxMemPerCPU=8192

# --- RQ4: priority preemption, requeue the victim ---
PreemptType=preempt/partition_prio
PreemptMode=REQUEUE
PriorityType=priority/multifactor

GresTypes=gpu
# No slurmdbd here, so sacct has no database to read. jobcomp/filetxt still
# gives per-job scheduler-side records (submit/start/end) without one; the
# headline timings come from the harness + job.sh ns stamps regardless.
AccountingStorageType=accounting_storage/none
JobCompType=jobcomp/filetxt
JobCompLoc=/var/log/slurm/jobcomp.log
JobAcctGatherType=jobacct_gather/none
SlurmctldLogFile=/var/log/slurm/slurmctld.log
SlurmdLogFile=/var/log/slurm/slurmd.log
SlurmctldDebug=info
SlurmdDebug=info

NodeName=$HOST CPUs=$CPUS RealMemory=$MEMMB Gres=gpu:$NGPU State=UNKNOWN
PartitionName=normal Nodes=ALL Default=YES MaxTime=INFINITE State=UP PriorityTier=1 PreemptMode=REQUEUE
PartitionName=high   Nodes=ALL MaxTime=INFINITE State=UP PriorityTier=100
EOF

mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm
systemctl enable --now slurmctld slurmd
sleep 3
scontrol update NodeName="$HOST" State=RESUME 2>/dev/null || true

echo "== sinfo =="
sinfo -o "%20N %10P %6t %10G %C"
echo "== 5s gres smoke job =="
srun --partition=normal --gres=gpu:1 --time=5 /opt/p6/job.sh slurmsmoke 5 slurm \
  && cat /var/log/p6/slurm_slurmsmoke.json
echo "slurm setup OK"
