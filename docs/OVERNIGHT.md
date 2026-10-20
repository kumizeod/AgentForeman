# Overnight assignments (v0.4)

Give a configured experiment project a goal at night. AgentForeman runs a detached
controller, asks Codex to plan and code, trains/evaluates actual artifacts, waits
without model calls when subscription usage is exhausted, and delivers a report
by the configured deadline. This is a single-machine, sequential experiment MVP.

## Submit tonight

Update the repository and install into your existing virtual environment:

```powershell
git pull
python -m pip install -e ".[demo]"
python -m agentforeman overnight start --project .\my-experiment --goal "Improve validation accuracy while preserving the checkpoint contract. Compare hypotheses and retain the best verified candidate." --deadline 08:00 --max-rounds 5
```

The same module command works on macOS/Linux; use the Python interpreter in your
environment. The project must already have a valid `foreman.toml`, source,
training command, trusted evaluator and metric. `init ./my-experiment` creates the
bundled CPU/NumPy demo in a new directory. Production uses your installed,
authenticated Codex CLI. A GUI-only Codex install is insufficient.

`08:00` means the NEXT 08:00 in the computer's local timezone. For an unambiguous
deadline, use a full ISO time with an offset, such as
`--deadline "2026-10-21T08:00:00+08:00"`. The timestamp is frozen when submitted.
The default is next local 08:00; the default round limit is 5 (maximum 100).
`--target 0.95` adds an explicit metric completion criterion.

The command prints a campaign ID and exits after the controller acknowledges
startup. Closing that terminal does not intentionally stop the detached controller.
The computer must remain powered on and awake, with network access for Codex.
OS shutdown, sleep, sandbox teardown or host policies can suspend/kill background
processes; restart with `overnight resume` after bringing the machine back.

## Check tomorrow

```powershell
python -m agentforeman overnight status CAMPAIGN_ID --project .\my-experiment
python -m agentforeman overnight report CAMPAIGN_ID --project .\my-experiment
python -m agentforeman overnight events CAMPAIGN_ID --project .\my-experiment
python -m agentforeman overnight list --project .\my-experiment
```

Files are under `.foreman/campaigns/CAMPAIGN_ID/`:

| File | Purpose |
| --- | --- |
| `report.md` / `report.json` | Best measurement/source/archive, rounds, deadline, quota waits and unfinished work |
| `controller.log` | Background controller output; model stdout is in each job's logs |
| `runner.json` | Process birth identity, startup token and controller exit/error status |
| `events.json` | Export of the campaign's transactional event history |
| `decisions/` | Planner prompts, answers, failed attempts and receipts |
| `project/.foreman/runs/` | Frozen child runs, real checkpoints and replay archives |

`status` includes `controller_alive`, `quota_wait.wake_at` (Unix seconds), the reset
source, pending child ID and current phase. Report files exist from submission.
A completed controller is not a guarantee that the research goal succeeded. The
report separately evaluates the configured target, or improvement over the original
baseline by `metric.min_delta`; arbitrary natural-language goals still need review.

## Quota handling

1. Accept an explicit usage-limit error from a FINISHED Codex CLI job. Model prose,
   training failures, authentication/network errors, timeouts, cancellations and
   a generic HTTP 429 do not trigger long quota waits.
2. Prefer the reset timestamp reported in the CLI error. Where supported, a short
   `codex app-server` connection initializes and calls ONLY `account/rateLimits/read`.
   It never starts a model thread or turn. The latest reset among exhausted Codex
   windows is used, covering secondary/weekly windows when returned by the CLI.
3. If no usable reset is available, wait five hours by default, then use increasing
   fallback backoff (capped at 24 hours). This is a conservative estimate, NOT a
   claim that every account resets five hours after failure. Override with
   `--quota-retry-seconds 18000`; disable the metadata lookup with `--no-quota-probe`.
4. Commit `waiting_quota`, phase, child ID, failed job ID, wake time and reason to
   SQLite. Return control to a plain Python timer. No model calls occur while
   waiting. A one-second local timer also notices stop requests.
5. On wake, reuse the same child run. A planner gets a new decision attempt; a coding
   worker gets a new CLI attempt over its preserved partial workspace. No baseline
   training is repeated. Partial edits must still satisfy the allowlist and frozen
   input hashes; otherwise recovery blocks for inspection. Previous worker handoffs
   and partial source are retained under `quota_attempts/`.

The coding worker continues the TASK from saved files and the original instruction,
in a new CLI session. This is not a guarantee of restoring all of Codex's private
conversation state. It never repeats a still-live/uncertain command. No paid API
fallback, credits purchase, account switching or quota reset redemption is performed.

Default maximum automatic quota resumptions: 8 per campaign. Set
`--max-quota-retries` to change it. Quota waits do not count as experiment rounds;
ordinary failed experiments do. A new campaign is independently bounded; there is
no account-wide scheduler coordinating concurrent campaigns in this version.

## Deadlines and recovery

Waiting time counts toward the morning deadline. If reset is after the deadline,
the controller waits only until the deadline and reports the existing best result
plus unfinished work. New job timeouts are capped to remaining time. Active jobs
are reconciled, and timeout cleanup/archive writes may finish shortly after the
deadline. No new experiment is started after the deadline.

```powershell
python -m agentforeman overnight resume CAMPAIGN_ID --project .\my-experiment
python -m agentforeman overnight stop CAMPAIGN_ID --project .\my-experiment
```

Resume launches a background controller if one is not alive; submitting resume
again while it is alive returns the existing receipt. A runner lock prevents two
controllers from owning the same campaign. Waiting state survives controller death;
resuming before wake does not spend a model call. Reboot recovery is manual; this
release does not install Windows services, LaunchAgents or systemd units.

`stop` writes a durable request: finish/reconcile the current round, launch no more
rounds, and report. It does not immediately kill a running training process. During
a quota wait it is noticed within about one second. Use the nested run's `cancel`
command for immediate job cancellation when needed (see CAMPAIGNS.md).

Unexpected errors remain visible and block rather than retry blindly. Inspect
`status`, `controller.log` and job logs; use `overnight resume --retry-failed` only
after resolving the failed stage. `--foreground` on start/resume keeps the controller
in the terminal for debugging. Existing `campaign` and single-run commands remain
available; campaigns created without the overnight policy keep their original
fail-fast behavior.

## Acceptance evidence

Tests inject explicitly labeled quota responses into test-only planner/editor
fixtures, use real CSV/NumPy training and an independent verifier, and check that
model invocation counts do not increase while waiting. Tests cover background CLI
submission, timer wake, controller death, duplicate launch, partial source continuity,
receipt reuse, deadline delivery and exhausted retry budgets. Tests use short clocks
or explicitly advance persisted wake/deadline timestamps, not an actual five-hour wait.
Live quota-exhaustion/reset behavior still needs verification on the user's CLI/account.

Protocol sources: [Codex non-interactive JSONL](https://developers.openai.com/codex/noninteractive),
[app-server initialization and rate-limit metadata](https://developers.openai.com/codex/app-server),
[plan limits and actual reset times](https://developers.openai.com/codex/pricing).
