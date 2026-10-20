# Campaign manager (v0.3)

For sleep-time submission, background execution, quota waiting and a morning
deadline, use the v0.4 [overnight command](OVERNIGHT.md). Campaigns created with
the commands below retain their original fail-fast quota behavior.

Submit a goal; the controller evaluates the original project, asks a model manager
for a decision, runs a bounded candidate, records independently verified evidence,
then asks for the next decision. This is a local, sequential Agent Manager MVP.

## Commands

Use the same environment's Python on Linux/macOS/Windows; no POSIX shell is required.

```bash
python -m agentforeman campaign start --project ./my-experiment --goal "Improve validation accuracy" --max-rounds 3 --budget-seconds 1800
python -m agentforeman campaign status CAMPAIGN_ID --project ./my-experiment
python -m agentforeman campaign events CAMPAIGN_ID --project ./my-experiment
python -m agentforeman campaign report CAMPAIGN_ID --project ./my-experiment
python -m agentforeman campaign resume CAMPAIGN_ID --project ./my-experiment
python -m agentforeman campaign list --project ./my-experiment
```

`--target 0.95` stops when the retained best metric reaches the threshold, respecting
maximize/minimize direction. `--until decide`, `execute` or `record` pauses BEFORE
that phase. Ctrl-C pauses the controller; detached active jobs keep running.
To cancel a child job, use the existing `cancel RUN_ID --project FROZEN_PROJECT`
command against the campaign's `project/` directory. Cancellation consumes a failed
round; it is not a campaign-wide stop command.

By default `[codex]` config supplies both independent CLI sessions. Optional
`[manager]` overrides `model`, `timeout_seconds`, `executable` or `command`. Manager
uses `--sandbox read-only`; coding worker uses `workspace-write`. Planning has
its own prompt, schema, evidence directory, answer and process receipts. The model
can inspect a read-only copy of the best source and its checkpoint contract.
It does not write campaign state. No model call is replaced by a fixture in production.

## Decisions and controller checks

| Action | Input code | Effect |
| --- | --- | --- |
| `experiment` | Best independently verified snapshot | One coding worker + training + verification |
| `repair` | Latest failed round's validated candidate | One fix, measured against a separately snapshotted healthy best baseline |
| `retest` | Best snapshot | Training and verification only; does not promote a lucky repeat score |
| `stop` | Evidence | Record reason and complete |

Every decision includes a nonempty instruction and rationale and valid completed
round references. Repair must reference exactly the latest round, and that round
must have failed during candidate training/verification with unchanged validated
candidate code. Protected-file edits, symlinks and missing/lost supervisor receipts
do not become repair seeds. The first repair version intentionally cannot repair
coding-worker failures before a validated candidate exists.

Promotion requires a strict improvement over the retained best metric of at least
`metric.min_delta`. Failed/worse rounds and their logs remain in history. Best code
and archive hashes are checked before further actions. Experiments remeasure their
baseline; this currently adds training overhead. Repair uses the healthy incumbent
as baseline so a broken seed does not prevent the coding worker from fixing it.
Repeated use of the same validation set can overfit; this is an engineering loop,
not a claim of scientific generalization. A held-out final test set is future work.

## Persistence and budgets

Campaign state/events live in the project's `.foreman/state.sqlite3`. Each campaign
has `.foreman/campaigns/ID/` containing frozen inputs, decisions, reports and nested
single-run archives under `project/.foreman/runs/`. Reports are summaries; each
completed child run has its own hashed replay ZIP. Evaluation-only archives use
`python reproduce.py --baseline`. Failed runs keep raw logs/source, not a success ZIP.

The state machine is `baseline → decide → execute → record → decide`, then `done`.
An exclusive campaign lock serializes controllers. The selected action, source hashes
and child ID are committed before creation/execution. Resume reuses that ID and the
original durable job specification/receipt; recording a round is one transaction.
If a process is still alive or its outcome is uncertain, the controller blocks
instead of replacing it. Completed campaigns only regenerate their summary on resume.

`--max-rounds` (1–100) counts failed, repair and retest attempts. `--budget-seconds`
sets an absolute elapsed wall-clock deadline at creation, including pauses and
baseline/planning/training time. Every new command's timeout is capped to remaining
time. Existing jobs are reconciled before stopping; atomic writes, archive generation
and process cleanup can finish after the deadline. This is not a hard CPU/GPU quota,
a token budget or a money limit. Budget expiry preserves interrupted job receipts
and produces a deadline summary; uncertain supervisor outcomes still block for
inspection. It does not authorize another model call.

## Failure recovery

Ordinary failed candidate jobs with settled receipts are recorded as failed rounds;
the model can propose a guarded repair or a fresh experiment. A malformed manager
answer blocks the campaign. After inspection, `campaign resume ID --retry-failed`
creates a new planner attempt and retains the previous answer/logs. It refuses to
retry a planner with no exit receipt or a still-live process.

A failed baseline or an uncertain child requires inspection of the nested single
run. Explicitly recover it using `resume RUN_ID --project FROZEN_PROJECT --retry-failed`
when safe, then resume the campaign with `--retry-failed`. Never delete receipt files
to force a replay. Changing the original project does not alter frozen inputs;
changing frozen inputs is rejected.

## 中文说明

这一版已经具备最小管理闭环：目标 → 模型提出下一步 → 程序校验动作和预算 →
Codex 改代码 → 真实训练 → 独立验证 → 记录结果 → 再决策。
失败候选可以修复，最佳候选单独保留，复测不会凭一次高分覆盖最佳记录。
退出后通过 `campaign resume` 恢复，同一轮使用原有任务 ID，不重复建任务。

它目前是单机、串行的 Agent Manager；远程 worker、GPU 调度、多租户和费用预算尚未实现。
测试中的管理决策与代码修改由明确标记的测试脚本产生，不能当作真实模型自主科研的证明。
