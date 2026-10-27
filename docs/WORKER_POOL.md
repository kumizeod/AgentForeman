# Member-owned overnight worker pool

v0.9 adds Claude Code/Gemini CLI workers, local LinJun-compatible API access and
[verified multi-agent code candidates](MULTI_AGENT.md). Existing Codex and API
worker configurations remain supported.

The coordinator stores worker-token hashes and task receipts. Each member runs a
pull worker using their own local Codex login. There is no credential upload,
shared auth.json, login-cookie proxy or automatic account creation. API workers
use separately billed API keys, kept in environment variables on that worker.

## Coordinator (one group computer)

Install the project, then register one unique random worker token per member.
In PowerShell, generate a token locally and pass it to that member through your
normal secure channel; do not commit or post it publicly:

```powershell
$env:MEMBER_A_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m agentforeman pool register --project .\my-experiment --member member-a --token-env MEMBER_A_TOKEN
```

Repeat for member-b, etc. Register `claude-chair` with its own token too. The API
key is not that token. This coordinator CLI is local administration; workers
cannot register members or submit arbitrary work through the network endpoint.

```powershell
python -m agentforeman pool serve --project .\my-experiment --port 8770
python -m agentforeman pool status --project .\my-experiment
```

The server stays in the foreground. Keep it and member workers running; sleep
mode suspends work. It binds to loopback by default. To connect other computers,
use an SSH tunnel to that loopback port or a TLS reverse proxy with the coordinator
behind it. The worker rejects cleartext remote origins and redirects. Do not
publish the unauthenticated local experiment UI to the network.

## Each member (Windows/macOS/Linux)

Run `codex login` as yourself. Copy `examples/pool/member.toml` and configure the
coordinator origin, a unique local state directory and optional local active hours.
Set `FOREMAN_WORKER_TOKEN` to the token registered for this member.

```powershell
python -m agentforeman pool worker --config .\member.toml
```

On macOS/Linux the same CLI works; set environment variables with `export` and use
normal POSIX paths. Use exactly one registered member identity per contributor
and one worker state directory per identity. Do not clone a member's worker state
onto another computer while it is running. Separate accounts do not necessarily
mean different models or independent reasoning. Share only tasks/data the group
has authorized those members and configured providers to see.

`active_hours = "22:00-08:00"` means the **worker's local clock**. It stops claiming
new work outside that window; existing work continues until its task deadline or
local timeout. `--once` attempts one claim, useful for diagnostics.

### Verify a real provider before an overnight task

Run this on a machine with a valid local Codex/Claude/Gemini login or API key:

```text
python -m agentforeman pool smoke --project ./my-experiment --config ./member.toml --timeout-seconds 90
```

This creates an isolated local HTTP pool, runs one bounded task through the
configured provider and native worker, and checks the returned sum independently.
It ignores production active hours and leaves the production coordinator and
worker state untouched. Credentials stay in the existing local login or
environment; no login files are copied. It can consume model/API quota.
AgentForeman does not retry this test automatically; the provider CLI may retry
connections within the timeout.

Exit code 0 means `passed: true`. Authentication failure, unavailable command,
quota waiting, provider failure and an incorrect answer return exit code 2.
A sanitized report is saved under `.foreman/smoke/ID/report.json`, with fixed
diagnostic codes rather than raw provider bodies or secrets. Do not publish raw
job logs. A passing arithmetic test validates transport and execution, not
multi-model independence or research quality.

`pool worker --once` prints its provider outcome and exits 2 for failed or
uncertain execution. Acknowledged upload is distinct from controller acceptance
and independent verification. No task and an inactive night window are explicit
non-calling states. Local `last-attempt.json` records the attempt status.
An uncertain job must be reconciled from its existing receipt, not blindly replayed.

If smoke reports `authentication_failed`, repair the login on that same worker
machine (for Codex, run `codex login` and then smoke again). A login file or
`codex login status` alone does not prove that the model service accepts it.
Do not send credentials or `auth.json` to the coordinator or a chat.

## Claude chair and other API workers

`examples/pool/chair.toml` uses the Anthropic Messages API. Select an explicit
Claude model ID supported by your API account; no unverified "best/latest" model
alias is imposed. Run it as another worker with `FOREMAN_CHAIR_TOKEN` and
`ANTHROPIC_API_KEY` set locally. The same worker can instead use
`provider = "openai-compatible"`, `key_env`, `base_url` (HTTPS, ending in `/v1`),
and an explicit `model`. That adapter uses Chat Completions JSON mode; providers
without that interface are not compatible. Never put a key value in TOML.

API requests have an output-token cap, timeout and no hidden retries. These are
not a dollar budget. Truncation, refusal and malformed JSON cannot become a
successful result. Raw API error bodies are not logged. A provider call uses the
existing detached native job supervisor, so a worker restart reattaches to the
receipt instead of issuing a new HTTP request blindly.

## Scheduling and recovery

Tasks carry an eligible-member list, immutable prompt/schema, deadline and lease.
SQLite transactions serialize claims across threads/processes. One member has at
most one active task, and cannot produce two counted answers in the same group
and stage. Different eligible members can run concurrently on different machines.

A quota failure releases that task, cools down the member until a reported reset
(or explicit fallback), and lets another eligible, unused member take over. Waiting
and polling make no model calls. Each task permits at most five quota attempts.
The Codex fallback is five hours, **not** a claim that all account limits reset
five hours after failure; API HTTP 429 uses Retry-After or a 30-minute fallback.

An expired heartbeat lease becomes **uncertain**. It is not reassigned, because
silently rerunning might charge twice. The original worker/session can reconnect,
reattach to its supervised job and upload the same result. Conflicting completion
receipts, invalid JSON/schema, another member's lease and expired task results are
rejected or marked expired. Late uploads are acknowledged without accepting the
output, so a completed outbox cannot permanently block a worker. A durable local outbox makes lost upload acknowledgments
safe to resend, including quota responses. Deadline expiry prevents new work.

The database is `.foreman/pool.sqlite3`; local worker state holds job specs,
prompts, answers and supervised process receipts. This initial pool dispatches
bounded read-only answer/review jobs. It does not ship arbitrary source workspaces
or distribute GPU training jobs. Existing single-machine campaigns are unchanged.

## References and test disclosure

Design inspiration: [karpathy/llm-council](https://github.com/karpathy/llm-council)
(independent responses, anonymous peer review, chairman synthesis). No upstream
implementation files were copied. This worker pool is an original implementation
using AgentForeman's existing supervisor and quota classifier.

Protocol references checked 2026-10-23:
[Codex environment](https://learn.chatgpt.com/docs/config-file/environment-variables),
[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create).
Tests use explicit CLI fixtures and mocked API responses, with real SQLite,
HTTP authentication, concurrent claims, local supervision and receipt recovery.
Live group logins, paid Claude responses and actual overnight resets still need
field testing with your members' credentials; no credentials are bundled.
