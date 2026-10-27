# Cooperative tasks and the shared dashboard

Cooperative mode supports bounded text, Markdown, JSON and CSV artifacts for
reports, analysis, design and similar complex tasks. It shares the adversarial
mode's independent checks, critique/revision/retest loop, budgets and preserved
disagreements.

## Roles and execution

1. The first member is the integrator. It writes a decomposition with exactly
   one assignment per contributor, concrete deliverables and an integration
   contract. This version supports independent parallel assignments, not a
   dependency graph of tasks.
2. Contributors independently produce bounded artifacts. Tasks are available
   concurrently; idle or rate-limited workers retain the existing pool's lease
   and cooldown behavior. Other contributors do not see peer answers.
3. All contributions are retained. The integrator receives the full set and
   produces combined artifacts. A missing, failed or invalid contribution blocks
   integration; partial results remain available for diagnosis.
4. Frozen independent checks run on the **combined** artifacts. Independent
   reviewers then issue anchored criticism. The integrator modifies the result
   and the controller retests it, within round/time limits.
5. The controller selects a candidate using check receipts. A separate chair
   summarizes only. Contributor, integrator, reviewer and chair identities must
   be distinct. Distinct identities do not imply statistically independent models.

## CLI

Register an integrator, contributors, at least one independent reviewer and a
chair in the existing pool. Keep `pool serve` and their workers running.
For example, with the sales evidence/check files from `examples/debate` and its
checker copied into the project as `check_total.py`:

```text
python -m agentforeman cooperate start --project ./my-project --question "Calculate sales total, explain limitations; return answer plus report.total integer" --members integrator calculator analyst --reviewers reviewer --chair chair --evidence examples/debate/evidence.json --checks examples/debate/checks.json --max-rounds 3
python -m agentforeman cooperate status TASK_ID --project ./my-project
python -m agentforeman cooperate report TASK_ID --project ./my-project
```

`list`, `advance` and `stop` mirror `debate`. State, contribution records, versioned
artifacts, receipts and reports are stored under `.foreman/cooperations/TASK_ID/`.
Restart the same coordinator and workers to resume; a fresh run is not necessary.

## Dashboard

Open the existing console with `python -m agentforeman ui --project ./my-project`.
In **共享委员会 → 合作与对抗任务**, choose a mode, goal, distinct role nodes,
evidence, frozen checks and budgets. The console currently uses a project with
`foreman.toml`; `pool serve` and workers run separately from the console.

The task list and detail panel show decomposition, contributions, candidate
versions, criticism, check receipts, progress and pending questions. You can stop
future stages and download the complete JSON transcript or Markdown report.
Reopening the console reads existing tasks. Model text is displayed as escaped
text, never executable HTML. Administrative APIs remain authenticated, same-origin
and loopback-only; worker credentials remain on their own machines.

The submission body is limited to 32 KiB. Python checkers must already exist as
trusted project-local scripts and are frozen before execution. A checker runs
locally under the coordinator account; it is not a sandbox for untrusted code.

## What a passing result means

`completed_scoped` certifies only the configured checks, with no remaining
unsupported criticism or protocol error. Agreement, a polished integration and
a chair's confidence do not establish factual correctness. Supplied source quotes
are matched against input text, not independently retrieved/authenticated.
Without checks, output remains `needs_evidence`.

This mode returns bounded artifacts; it does not grant remote agents arbitrary
filesystem or shell access. Use the existing coding candidate pipeline for actual
project code modification, training and verified code archives.

## Reproducible validation

```text
python -m unittest discover -s tests -v
npm ci --prefix tests/browser
npx --prefix tests/browser playwright install chromium
npm test --prefix tests/browser
```

Browser tests submit both modes through the actual form, use concurrent HTTP
workers with native supervised **scripted model replies**, execute a real Python
sum checker, reproduce a failed combined result, critique it, modify it and retest.
They also check escaped content, stop requests, JSON/report downloads and mobile
layout. They do not call paid models or measure live model/research quality.
