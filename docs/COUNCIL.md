# Overnight research council

The council runs on the member-owned [worker pool](WORKER_POOL.md):

1. Independent exploration: each counted answer must come from a different member.
   No member sees another answer during this stage.
2. Anonymous peer review: each author evaluates all other accepted answers, never
   their own. Labels are deterministically shuffled per reviewer and kept in the
   transcript. Every answer must be scored and ranked exactly once.
3. Chair synthesis: a separate identity (typically the Claude API worker) receives
   anonymous answers, reviews, advisory ranks and all major/blocking findings.
   Its structured output must respond to every required finding ID exactly once.
4. Independent audit: a member identity separate from the chair checks the final
   answer against original sources, answers and criticism. A rejection cannot be
   silently promoted to success.

There is no human approval stage. Four bounded stages replace an unbounded chat.
The existing single-experiment proposal/revision workflow remains available.

## Start

Register at least two member identities and a separate `claude-chair` identity,
then run `pool serve` and the corresponding member/chair workers. The coordinator
advances stages automatically; restarting it resumes saved work without recreating
model jobs. Set the chair worker's explicit Claude model ID in its local TOML.

```powershell
python -m agentforeman council start --project .\my-experiment --question "Which controlled experiment should we run to test this idea?" --members member-a member-b --chair claude-chair --budget-seconds 28800 --stage-seconds 1800
python -m agentforeman council list --project .\my-experiment
python -m agentforeman council status COUNCIL_ID --project .\my-experiment
python -m agentforeman council report COUNCIL_ID --project .\my-experiment
```

`council advance COUNCIL_ID` manually advances one durable transition if the pool
server is not running. `council stop COUNCIL_ID` prevents future stages/queued
claims. Already-running provider calls remain bounded by their original timeout
and stage deadline; stop does not promise to undo a billable API request.

Each stage waits for all responses to settle or its deadline. Exploration and
review require at least two valid answers/reviews (`--min-answers`), synthesis and
audit require one. Missing/invalid results are retained in the pool; insufficient
quorum ends with `incomplete`, not an invented consensus. A stage can use a partial
quorum after its timeout. One unavailable member cannot stall forever. Global
budget expiry also stops transitions.

## Local panel

Start `python -m agentforeman ui --project .\my-experiment` and open **共享委员会**.
It shows registered members, last-seen state and quota cooldown, accepts questions,
member selection, a separate chair and an optional source catalog, and displays
independent answers, peer reviews, final answer, audit and JSON export. Stop blocks
future stages. Registration and credentials remain local CLI/environment operations.
The panel does not replace `pool serve` or launch remote workers by itself.

## Sources and quality gates

Supply a JSON evidence catalog with `--sources sources.json`, for example:

```json
[
  {"id":"S1","title":"Paper title","url":"https://example.org/paper","excerpt":"Relevant source text you have checked"}
]
```

The catalog is snapshotted and hashed, then sent identically to all roles. Hashing
preserves provenance; it **does not verify** whether an excerpt matches a web page.
This release does not implement literature search or source downloading. A model
cannot create a valid source ID merely by inventing a citation. Unknown source or
supporting-answer IDs, incomplete/duplicate rankings, and missing responses to
major findings fail protocol checks.

The chair must distinguish claims, supporting answers, remaining questions and
proposed experiments. Source IDs refer to the supplied catalog, not to independent
confirmation of the claim. The audit can demand more evidence or reject the answer.
`completed_candidate` means structural gates and advisory audit passed; it is not
"verified truth". Missing sources, outstanding findings, audit uncertainty,
unsupported claims or missing required tests yield `needs_evidence`.
`quality.factual_correctness_verified` and `quality.experiments_executed` remain
false. Proposed baseline/ablation tasks are recorded, not fabricated as completed.

Same-model accounts can share biases. Identity labels hide routing metadata, not
all clues in model prose. Numeric novelty/impact/feasibility scores and rankings
are advisory; they do not prove novelty or quality. We adapt llm-council's useful
workflow and add inspectable safeguards rather than promise that agreement is
correct. The chair can be configured as Claude; a registration name alone does
not attest which model a member actually runs.

## Artifacts and failure recovery

`.foreman/councils/<id>/` contains `state.json`, full `transcript.json` and readable
`report.md`. The transcript includes original answers, anonymous label mappings,
reviews, required findings, chair responses, audit, protocol errors and quality
flags. Worker-native receipts are on each worker machine; the coordinator stores
submitted results and lease history in its SQLite database.

Tasks use deterministic IDs and immutable requests. A crash after enqueue but
before advancing state reuses those same tasks. Per-council locks prevent two
controllers from generating duplicate stages. A lost lease is uncertain, never
silently reassigned. Quota cooldown and alternate-member scheduling come from
the shared pool. No human research-direction gate is added.

There are at most `2*N + 2` successful model tasks (N independent answers, up to N
peer reviews, one chair and one audit), plus bounded quota attempts. Time and
output-token limits are not a token-dollar budget. Remote members' code-edit and
GPU training jobs are not distributed by this council; local verified experiments
still use the existing campaign workflow.

## Attribution and validation

Inspired by [karpathy/llm-council](https://github.com/karpathy/llm-council)'s
independent answers, anonymous review and chairman synthesis. Inspected README,
backend/council.py and backend/config.py on 2026-10-23. No implementation files
were copied. Our orchestration, protocol, durable queue and audit are original.

Tests use explicit answers and mocked provider responses. They exercise real
SQLite, locks, HTTP, process supervision and file receipts. Live overnight group
participation and paid model answer quality require field evaluation; no quality
improvement percentage is inferred from fixture tests.
