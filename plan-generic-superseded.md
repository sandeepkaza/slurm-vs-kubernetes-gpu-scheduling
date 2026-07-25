# Paper 6 — "From Spike to Pod: Reaction Latency and the Cost of Kubernetes Autoscaling"

**Genre:** controlled benchmark + cost model. **Venue path:** UCC → IC2E → IEEE Access.
**Gap (web-verified):** HPA/KEDA guidance is folklore ("HPA reacts late",
"KEDA evaluates every 30 s") and academic work builds smarter autoscalers;
nobody publishes a clean reaction-latency decomposition of the standard
stack under a fixed burst workload.

## RQs
- RQ1: Reaction decomposition per stack (HPA-CPU, HPA-custom, KEDA queue
  trigger, KEDA scale-from-zero): burst start → metric visible → decision →
  pod scheduled → pod Ready → first request served. Client-observed, seq
  probe (Paper 3 writer pattern → request loss countable).
- RQ2: Parameter sensitivity — polling/stabilization windows vs reaction time
  and flapping (tunables, measured not guessed).
- RQ3: Scale-from-zero cold path — KEDA activator gap vs warm floor (1 pod)
  vs overprovisioned floor; request loss during the gap.
- RQ4 (FinOps): dollars of the trade — overprovision floor cost vs SLO
  violation window, from measured curves + real node pricing; "cost of one
  second of reaction time".

## Testbed
kind (dev) → one managed cloud (AKS or EKS, small nodes) for the paper
numbers. Workload: boutique frontend + synthetic burst generator (step +
spike + diurnal patterns); Redis queue for KEDA event trigger.

## Cost
Single small cluster, hours. <$15.

## Status
- [ ] Burst harness + per-phase timestamping
- [ ] Runs: 4 stacks × 3 burst shapes × ≥5 trials
- [ ] results.md → user validates → 10pp draft
