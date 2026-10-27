# Adversarial mode: initial CLI checkpoint

This initial implementation supports general text, JSON and CSV task artifacts,
including reports and data analysis. It uses the existing member worker pool.
The first member proposes, the remaining members critique, and a separate chair
summarizes. The dashboard does not yet expose this mode.

## Run the supplied sales example

Install AgentForeman and register `author`, `critic`, and `chair` in the existing
pool. Keep `pool serve` and their member workers running for the same project.
Copy `examples/debate/check_total.py` into that project as `check_total.py`.
From the repository root, run (one line works in Windows PowerShell, macOS and Linux):

```text
python -m agentforeman debate start --project ./my-project --question "Compute the supplied sales total; include an answer artifact and a report JSON artifact with integer field total." --members author critic --chair chair --evidence examples/debate/evidence.json --checks examples/debate/checks.json --max-rounds 3
python -m agentforeman debate status DEBATE_ID --project ./my-project
python -m agentforeman debate report DEBATE_ID --project ./my-project
```

`debate list`, `advance`, and `stop` are also available. The server advances active
debates automatically. Durable state, versioned artifacts, check receipts and the
final report live under `.foreman/debates/DEBATE_ID/`; restart the server and
workers to continue unfinished tasks. Existing pool lease/retry rules apply.

## Controls implemented

- Criticism must quote an existing artifact, supplied source or check receipt,
  with matching line numbers. Invalid criticism is retained as a protocol error.
- Each revision must change normalized artifacts or add valid anchored support.
  Repeated artifacts without new support stop the debate; wording in the summary
  or response alone does not count as progress.
- Round and time budgets bound execution. Reports preserve unresolved issues.
- The controller writes a `pending_questions` list, including unresolved
  criticism, failed or missing checks, absent verification coverage and protocol
  errors. A chair cannot remove these follow-ups by omitting them from its summary.
- The progress ledger records each verification stage, its candidate, passed
  checks and reused receipts. Cached results are explicitly distinguished from
  new check executions.
- Checks are configured before debate and frozen. Supported checks are JSON value
  equality, literal containment and a copied Python checker. Models cannot submit
  executable check commands. Receipts are reused for identical check inputs.
- Only a failed-check-linked issue can become `check_passed_scoped` after retest.
  Other concerns remain unverified. A chair summary cannot override verification
  or replace the controller-selected candidate.

`completed_scoped` means the configured checks passed, critics accepted and no
unresolved issue or protocol error remains. It does **not** establish general
factual correctness. Other outcomes are retained as `needs_evidence`.

## Scope and next work

This is a core/CLI checkpoint, not a completed end-to-end research product.
Source matching checks supplied text only; it does not retrieve literature or
authenticate sources. Artifact changes do not establish semantic improvement.
Python checkers are trusted local programs with timeouts, not sandboxed programs.
Configure them only from code you trust; standalone checkers receive artifact,
evidence and output paths as shown in the example.

Dashboard integration, additional task adapters, live model/overnight evaluation
and a fresh full Windows/macOS/Linux regression run remain follow-up work.

Run the targeted control and recovery tests with:

```text
python -m unittest tests.test_debate -v
```

These tests use scripted member replies and real controller/check execution;
they do not establish the quality of live model criticism or research conclusions.
