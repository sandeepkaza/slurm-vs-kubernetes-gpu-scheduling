# gpu-scheduler-measurement

Companion artifact for the paper **"Slurm or Kubernetes for GPU Clusters? A Same-Host, Same-Trace Measurement of Scheduling Behavior"**.

Same-host, same-trace measurement of Slurm vs Kubernetes (and Kueue) GPU scheduling: queue wait, gang scheduling, and preemption, with GPU-utilization traces gating every leg.

## What's here

`data/results_*.csv` — per-job timings for all 5 legs. `data/gpuutil_*.csv` — GPU-utilization traces proving each leg loaded the GPUs. `data/trace.json` — the seeded job trace. `harness/` — trace generator, stack installers, submit/measure drivers.

## Reproduce

Requires Python 3 (plus, per paper, Docker/kind/a GPU host — see the paper's
Method section). Every number in the paper is regenerated from the committed
data by:

```bash
cd data && python3 analyze.py results_slurm.csv results_slurm_preempt.csv results_k8s.csv results_k8s_preempt.csv results_k8s_kueue.csv
```

Each stage writes its intermediate data, so the path from raw measurement to
every reported figure is inspectable without re-running the measurement.

## Citation

See `CITATION.cff`. A DOI will be added after Zenodo archival. Venue path:
UCC -> IC2E/HPC -> IEEE Access.

## License

MIT (`LICENSE`). Author: Venkata Sandeep Chowdary Kaza <sandeep.kaza@gmail.com>, ORCID 0009-0001-9454-8995.

## AI-assistance disclosure

AI-assisted scripting and editing (Anthropic Claude) was employed under the
author's direction; the author defined the research questions, designed the
method, ran the measurements, and validated every reported number.
