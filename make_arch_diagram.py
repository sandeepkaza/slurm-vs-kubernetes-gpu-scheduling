"""fig_testbed.png -- Paper 6 testbed + measurement harness (diagrams+graphviz).

One trace, one binary, one host; both schedulers measured at the same two
clocks. Requires graphviz `dot` on PATH.
Run: python make_arch_diagram.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.programming.language import Python
from diagrams.generic.storage import Storage
from diagrams.onprem.compute import Server
from diagrams.onprem.container import Docker

graph_attr = {"dpi": "300", "fontsize": "13", "pad": "0.2", "splines": "spline",
              "nodesep": "0.35", "ranksep": "0.6"}
node_attr = {"fontsize": "11"}

with Diagram("", filename="fig_testbed", outformat="png", show=False,
             graph_attr=graph_attr, node_attr=node_attr, direction="LR"):

    trace = Python("trace_gen.py\n40 jobs, seeded Poisson\nwidths 1&2, 20% high-prio\noffered load 2.31x")

    with Cluster("one KVM VM  (2x RTX A5000, driver 580, cgroup v2)"):
        burn = Storage("/opt/p6/gpu_burn + job.sh\nONE binary, ns start/end stamps\n+ bundled CUDA libs")

        with Cluster("leg A: Slurm  (slurmd)"):
            slurm = Server("sbatch, gres=gpu:N\nbackfill + REQUEUE preempt\nhigh/normal partitions")

        with Cluster("leg B: Kubernetes  (k3s, slurmd stopped)"):
            k8s = Docker("Jobs, nvidia device-plugin\ndefault sched | +Kueue queue\nPriorityClass preempt")

    with Cluster("measurement"):
        run = Python("run_slurm.py / run_k8s.py\nsubmit_ns @ apply\n+ GPU-util sampler (gate)")
        analyze = Python("analyze.py\nwait, makespan, gang,\nbackfill, preempt survival")

    out = Storage("results_*.csv\ngpuutil_*.csv\nanalysis.txt")

    trace >> Edge(label="same trace") >> slurm
    trace >> Edge(label="same trace") >> k8s
    burn >> slurm
    burn >> k8s
    slurm >> Edge(label="one at a time") >> run
    k8s >> run
    run >> analyze >> out
