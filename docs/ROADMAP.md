# Roadmap

Implemented through v0.3: real Codex CLI adapters, baseline/candidate jobs,
independent artifact verification, archive/replay, controller crash recovery,
bounded multi-round campaigns with model decisions (experiment/repair/retest/stop),
persisted evidence, best retention and elapsed wall-clock budgets.
Live authenticated model execution still needs verification.

Next milestones, in suggested order:

1. Execute the configured native Windows/macOS/Linux CI matrix and run real
   authenticated Codex on each platform. Record CLI
   version, sandbox behavior, completed handoff and model usage evidence.
2. Add a PyTorch/CUDA example with a well-defined external evaluator and model
   checkpoint continuation. Verify it on an actual GPU.
3. Extract worker/runner/verifier protocols from the concrete modules once a
   second implementation is needed.
4. Extend the existing campaign with token/cost accounting, held-out final tests,
   multi-seed comparisons and baseline-reuse policies with explicit provenance.
5. Add controlled automatic recovery policies plus fault-injection evaluations;
   compare against fixed retry and single-worker baselines.
6. Add a small web UI, notifications, worker pools, GPU leases and task dependency
   scheduling. Introduce container isolation before accepting untrusted workloads.
7. Investigate learned management policies only after collecting reliable
   decision/action/outcome traces. Measure final verified results and human
   intervention time, rather than token consumption as a success metric.

Out of scope now: training a new LLM, generic neural architecture search,
distributed execution, managed cloud hosting, auto-merge/deployment, account
sharing, or claims of unattended operation across arbitrary failures.
