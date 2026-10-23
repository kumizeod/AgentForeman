# Bounded automatic experiment review (v0.7)

A proposer and a reviewer now use separate read-only Codex sessions before a
coding worker can execute a candidate. There is **no human approval stage**.
The controller owns the budget, transitions, execution and independent verifier.

## Use from the panel or CLI

Start the existing local panel and submit a new overnight task:

```powershell
python -m agentforeman ui --project .\my-experiment
```

The form enables **自动双角色审查** by default and allows 1–5 reviews per proposal
(default 2). The task detail's **方案审查** tab shows hypotheses, controls, expected
observations, failure criteria, criticism, proposer responses and verdicts.
**导出审查** downloads the transcript JSON. The logs selector exposes the reviewer
and revision jobs separately. No page asks for approval to continue a discussion.

CLI review is opt-in, preserving existing scripts and old campaigns:

```powershell
python -m agentforeman overnight start --project .\my-experiment --goal "Improve accuracy under the existing validation protocol" --deadline 08:00 --max-rounds 5 --review --max-reviews 2
```

Foreground `campaign start` accepts the same two flags. For debugging,
`--until review` or `--until revise` pauses a foreground campaign before that
phase. The HTTP API accepts `review: true` and `max_reviews: 2`; absent `review`
keeps older API clients on their existing single-proposer behavior.

## Automatic state transitions

1. Evaluate the baseline and preserve a hashed, independently verified archive.
2. The proposer returns a decision plus a structured experiment plan: hypothesis,
   expected observation, comparison and failure criterion.
3. A fresh reviewer session inspects evidence, the healthy incumbent and the
   actual edit seed (including a failed candidate for repair). Each concern must
   include severity, evidence and a concrete suggestion.
4. **Approve** allocates the experiment. An approval with unresolved major or
   blocking concerns is a protocol error and cannot launch a worker.
5. **Revise** sends the previous proposal and criticism back to the proposer.
   A revision must explicitly respond, then undergo a new review.
6. **Reject**, or a revise verdict on the final allowed review, automatically
   ends the campaign and retains its best verified result. The proposer can also
   choose stop. There is no automatic fallback that executes an unapproved plan.
7. Accepted experiments use the existing code-edit, training, independent
   verification and archive pipeline. Each new experiment/repair/retest proposal
   is reviewed again; measurement and best-result promotion remain programmatic.

`max_reviews=2` permits two successful reviews and at most one intervening
revision. Initial proposal + review + revision + second review costs up to four
planning/review model jobs before the coding worker, subject to the campaign
wall-clock deadline. This is not a hard token/money budget. Explicit failed-job
retries and quota resumptions are separate recorded attempts; they do not grant
additional successful discussion rounds. A final manager stop decision needs no
review because it allocates no experiment.

## Roles and configuration

The initial implementation uses the existing **Codex CLI protocol** for all
three roles. It does not integrate the Claude CLI. Separate sessions can use
different configured Codex-accessible models:

```toml
# In addition to the existing [codex], [train], [verify] and [metric] tables:
[manager]
# model = "a-model-available-to-your-account"
timeout_seconds = 600

[reviewer]
# model = "another-model-available-to-your-account"
timeout_seconds = 300
```

Both roles inherit `[codex]` settings, then apply their own overrides. Without a
model override, the same configured model serves two separate sessions with
different instructions. Session separation does not guarantee independent
reasoning, true novelty or factual correctness. The reviewer challenges
experimental design; this version does not add a literature-retrieval service.

The user goal, dataset split, editable allowlist, trainer/verifier commands,
metric and budget stay frozen by the existing controller. The reviewer prompt
explicitly treats goals, logs, previous reviews and proposals as untrusted data.
A proposal's SHA-256 binds its approval to the allocated decision. Changed
instructions after approval are rejected before candidate execution. This guards
handoff integrity, not semantic correctness of every suggestion.

## Quota, deadlines, recovery and evidence

Review and revision jobs have their own durable specs, native supervised process
receipts, output schemas, prompts and read-only contexts. Restarting reattaches
the same job or consumes its existing result. A crash after review completion but
before state commit reuses that receipt instead of asking the model again.

A reviewer/revision usage-limit error saves the exact discussion stage and waits
without model calls. Wakeup increments only that stage's attempt number and
preserves previous successful reviews and baseline receipts. Optional preflight
checks also apply before new discussion jobs. Overall quota retry and wall-clock
limits still apply. Stop requests/deadlines terminate the campaign without
starting further discussion/worker jobs. Existing abnormal-process uncertainty
still blocks unsafe replay; this is error recovery, not a research approval gate.

Each campaign writes:

- `discussion.json`: completed/active discussions, full proposals and responses,
  concerns, verdicts and proposal hashes.
- `discussions/<round>/review-<index>-<attempt>/`: reviewer context and job evidence.
- `discussions/<round>/revise-<index>-<attempt>/`: revised proposal and feedback.
- `report.md`: review counts, summaries, concerns and automatic stop reason.

Campaign evidence remains alongside the existing per-run replay ZIPs. The best
model ZIP is not modified to insert a transcript after its hash is established.
Use **导出审查** when sharing the discussion separately. Local statistics and quota
views now include a reviewer role; revision usage belongs to the manager.

Validation uses explicitly named planner/editor/reviewer/quota fixtures. Real
CSV optimization, independent checkpoint validation, native supervision,
recovery and downloaded archives execute for real. Live authenticated model
criticism quality and real subscription reset timing still need field validation.
