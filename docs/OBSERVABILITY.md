# Quota visibility and local operations (v0.5)

AgentForeman borrows the useful operational ideas from
[170-carry/codex-tools](https://github.com/170-carry/codex-tools): visible independent
quota windows, explicit cache freshness, token accounting, CLI diagnostics and
an at-a-glance panel. This is an independent Python implementation integrated
with the durable experiment manager, with no additional runtime dependencies.

## Before submitting overnight work

```powershell
python -m agentforeman doctor --project .\my-experiment --json
python -m agentforeman usage --project .\my-experiment --role worker --refresh --json
python -m agentforeman usage --project .\my-experiment --role manager --refresh --json
```

`usage --refresh` launches the configured Codex executable's `app-server` and
uses **only** `initialize`, `initialized`, and `account/rateLimits/read`.
It never creates a thread or turn. Codex manages its own existing login.
AgentForeman does not read, copy or modify auth.json/config.toml credentials.
There are no warm-up prompts, purchased resets or paid API fallbacks.

The result reports primary and secondary windows independently, including zero
usage, remaining percentage, actual duration in minutes, Unix reset time, and
plan type when available. It selects the `codex` bucket and excludes other
model buckets. Missing windows are unknown; their duration is never assumed to
be five hours or one week. Tokens do not convert to subscription percentages.

Without `--refresh`, `usage` reads the local cache only (`--cached` is explicit).
Snapshots include `fetched_at`, `attempted_at`, `age_seconds`, `stale`,
`reset_passed` and a sanitized error category. Freshness defaults to five
minutes. A failed refresh retains the last good snapshot and marks it stale.
Passing a reset time also marks a snapshot stale: cached percentages are never
changed to zero without a new server response. Exit code 2 means unavailable or
an explicitly requested refresh failed; cached stale snapshots remain readable.

Worker and manager caches are separate and bound to an opaque hash of their
settings and CODEX_HOME. No raw RPC payload or arbitrary server error text is
stored. If you change accounts in the same Codex home, explicitly refresh both
roles; cached snapshots cannot detect that change and do not prove entitlement.

## Optional proactive quota guard

```powershell
python -m agentforeman overnight start --project .\my-experiment --goal "Improve validation accuracy" --deadline 08:00 --max-rounds 5 --quota-preflight
```

Before each **new** manager/worker job, `--quota-preflight` requests fresh
metadata once. If a Codex window is exhausted and has a future reset, the
controller durably enters `waiting_quota` before launching a model. When both
windows block, it waits for the latest reset. Waiting itself only runs a local
Python timer, with no model calls or metadata polling. Stop/deadline still apply.

This option defaults off and requires probing enabled; it cannot be combined
with `--no-quota-probe`. Unsupported/failed metadata checks fall through to the
existing job path, where explicit CLI usage-limit errors still trigger durable
waiting. Metadata checks are advisory, since quotas can change after a read.
Existing supervised jobs always reattach to their original receipts and are
never replaced by a preflight check. A preflight pause preserves baseline
training receipts and resumes the unlaunched job without a failed-job retry or
partial-edit recovery. Waiting/retry limits apply to both kinds of quota pause.

## Monitor without spending model quota

```powershell
python -m agentforeman monitor --project .\my-experiment
python -m agentforeman monitor --project .\my-experiment --watch
python -m agentforeman monitor --project .\my-experiment --campaign CAMPAIGN_ID --watch --json --samples 3
```

The panel shows campaign/child phases, controller birth-identity liveness,
completed rounds, best verified metric, deadline, quota wake time, stop request,
and whether an overnight controller needs manual resumption. Quota values are
cached only. `--watch` refreshes local state every five seconds; `--interval`
must be at least one second. `--json --watch` emits one JSON object per line.
`--samples N` makes watch mode finite. Ctrl-C stops monitoring only.

Foreground campaigns have no detached runner, so `controller_alive` describes
the detached runner only. Resume hints apply to overnight campaigns. Monitoring
does not install a service, wake a sleeping computer or restart controllers.

## Token accounting and support report

```powershell
python -m agentforeman stats --project .\my-experiment --json
python -m agentforeman diagnostics --project .\my-experiment --json
```

`stats` counts only observed `turn.completed.usage` records in known manager and
worker job logs. It separates input, cached input, output, role and coverage.
Cached input is a subset of input, so total tokens equal input plus output.
Missing/invalid records are reported through incomplete coverage, never treated
as proof that a job consumed zero tokens. Retries are separate model jobs;
archived/quota snapshots and training output cannot double-count usage.

Reads are bounded to the last 32 MiB per log with a 1 MiB per-record bound;
oversized/malformed logs are marked incomplete. This is a partial local ledger,
not account-wide billing. No dollar estimate or utilization percentage is
invented. Quota wait seconds include the current durable wait; simultaneous
campaign waits can overlap and their sum is not elapsed project time.

`doctor` checks config, platform and both manager/worker executable resolution.
`diagnostics` adds the local statistical summary. Output deliberately includes
only allowlisted fields: no prompts, goals, raw command arguments, absolute
paths, account identifiers, credentials or raw server error text. Login and
sandbox operation still require a real Codex run.

All commands support native Windows, macOS and Linux using the existing Python
runtime and process adapter. No shell, terminal UI library or web server is
required. For PowerShell, use `python -m agentforeman` from the active venv.

## Reference scope

Reviewed upstream commit `b8f2dbb829288ce7b1c978925062bef58fd3291e`
(2026-10-21), MIT, copyright 2026 170-carry. Reviewed README, LICENSE,
`src-tauri/src/usage.rs`, `UsageFreshnessBadge.tsx` and `AnalyticsSummary.tsx`.
No third-party code was copied. The upstream internal HTTP usage endpoints and
credential management are not dependencies. AgentForeman uses the official
[Codex app-server contract](https://learn.chatgpt.com/docs/app-server).

Account pools/provider switching, API proxy/tunnels, account warm-up and native
tray applications are deferred. AgentForeman's present priority is one
researcher's verifiable overnight experiment loop. Cross-platform Python CLI
operations preserve that focus; a desktop UI can later consume JSON output.
