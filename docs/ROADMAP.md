# Roadmap

v0.8 implements member-owned Codex/API pull workers, durable leases, per-member
quota cooldown, alternate eligible members, local night windows and a shared
council panel. Councils run independent answers, anonymous non-self peer review,
a configurable Claude API chair and independent audit. Optional delayed start and
local verified-experiment handoff connect research advice to actual measurements.
See [worker pool](WORKER_POOL.md) and [council](COUNCIL.md).

v0.7 implements bounded proposal/review/revision, issue tracking and structural
stagnation detection, without human approval. v0.6 provides the local experiment
panel. Earlier releases provide code edits, real training, independent artifact
verification, archive/replay, native process recovery and model-free quota waits.

Next priorities:

1. Field-test at least two authorized group-member workers and the configured
   Claude API chair overnight. Record platform/CLI/model versions, actual quota
   reset timing, job receipts and observed costs without exposing credentials.
2. Evaluate answer quality against single-model and plain-council baselines on
   independently scored tasks. Measure invalid claims, missing controls and
   verified experiment outcomes; do not infer gains from fixture tests or votes.
3. Add actual literature retrieval with source-content verification, structured
   claim-to-evidence links, and final synthesis conditioned on experiment results.
4. Extend local experiments with held-out tests, multi-seed comparisons, ablations
   and enforceable token/API-money budgets. A fixed job count is not a cost cap.
5. Add remote code-workspace/GPU execution only with explicit workspace isolation,
   resource limits and artifact verification. Read-only answer workers are already
   remote; GPU jobs are still local.
6. Add fault-injection benchmarks and management-policy comparisons after enough
   decision/action/outcome traces exist. Learn scheduling only when there is a
   measured advantage over fixed policies.

Out of scope now: training a new LLM, shared login-cookie repositories, managed
cloud hosting, auto-merge/deployment, guaranteed research novelty or correctness,
and unattended recovery from arbitrary ambiguous side effects.
