# Recovery semantics

| Event | Behavior on resume |
| --- | --- |
| Ctrl-C manager | Reattach to active supervisor; finished stages reuse receipts |
| SIGKILL manager | Same reconciliation; stored status may remain running until resume |
| Command finishes while manager is absent | Consume its atomic result without rerunning it |
| Multiple managers for one run | Exclusive run lock rejects the second manager |
| Multiple supervisors for one job | Job lock allows only one to execute the command |
| Training/worker timeout | Kill command process group; persist timeout; require explicit retry |
| Explicit cancellation | Supervisor terminates command process group and records cancellation |
| CLI missing before launch | Fail visibly; install CLI, then explicit retry is supported |
| CLI login/limit/error exit | Keep stderr; no simulated fallback; explicit retry |
| Supervisor disappears, child still alive | Fail as ambiguous; never launch another training job automatically |
| Supervisor disappears, child gone | Fail as ambiguous; inspect output before explicit retry |
| Host reboot | Boot identity detects stale PIDs; interrupted job is ambiguous, not silently replayed |
| Archive interrupted | Rebuild archive from existing verified evidence |

## Retry is an explicit new attempt

`resume --retry-failed` retries the current failed phase. It refuses to retry while
the previously recorded supervisor or child is alive. Editing retries restore
the worker directory from the original frozen source; training retries move old
outputs into `failed_outputs` and start with a fresh output directory. Failed
verification removes stale verdict output before invoking the evaluator again.

Model optimizer/checkpoint continuation is not implemented generically: normal
manager exit preserves a running training job, but an interrupted training
process restarts training when explicitly retried. A future runner contract can
support resuming a model-specific checkpoint.

If the supervisor died in the tiny interval after spawning a child but before its
identity was recorded in `process.json`, inspect `child.json` as well. The process
receipt and command logs must be reconciled before retry. If a spec exists with
neither process nor result receipt, the CLI deliberately refuses a blind retry;
inspect `supervisor.log`. There is no generic exactly-once guarantee for arbitrary
external effects. Database/job IDs and locks prevent the supported duplicate
launch cases; they do not make external APIs transactional.

## Inspecting jobs

Each `.foreman/runs/ID/jobs/PHASE-NN` contains:

- `spec.json`: executable/argv, workdir, timeout and non-secret environment overrides.
- `process.json`: supervisor and child identity, start time.
- `child.json`: self-recorded child identity before exec.
- `stdout.log`, `stderr.log`: raw command output.
- `supervisor.log`: supervisor failures independent of command stderr.
- `result.json`: durable exit receipt, timeout/cancel flags, duration.

Do not delete these files to make a run appear successful. If an operator must
resolve an ambiguous state manually, preserve a copy of the original run and
create a new run for further work.

## Per-job budgets

This milestone enforces separate wall-clock timeouts for Codex, training and
verification. Baseline and candidate each get the configured training timeout.
There is no total GPU-hour, token-dollar or campaign budget accounting yet.
Time spent with the manager paused is not itself charged by AgentForeman; an
already running command remains subject to its supervisor's timeout.
