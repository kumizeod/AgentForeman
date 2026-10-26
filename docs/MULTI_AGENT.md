# Multiple coding agents (v0.9)

[LinJun](https://github.com/wangdabaoqq/LinJun) manages CLIProxyAPI accounts,
provider routing and coding-tool configuration. AgentForeman borrows the separation
between tool adapters, routing and task management. It does not copy LinJun's
implementation, read authentication files, or implement its OAuth proxy.

## CLI workers

The same member-owned pool now accepts `provider = "codex"`, `"claude-code"`,
`"gemini-cli"`, `"anthropic"`, or `"openai-compatible"`. Register a distinct member
for each contributing worker, then use the existing worker command:

```powershell
python -m agentforeman pool agents
python -m agentforeman pool worker --config .\claude-code.toml
python -m agentforeman pool worker --config .\gemini-cli.toml
```

Examples are in `examples/pool/`. Log in using each CLI's own supported flow first.
`pool agents` detects executable availability only; it never calls a model, reads
login files, or claims an account is authenticated. Windows npm shims resolve via
the corresponding package manifest and Node argv, without cmd.exe/shell parsing.

CLI agents participate in independent exploration, anonymous peer review and audit
using the same immutable tasks, leases, heartbeats and durable receipts. The chair
can remain the separate Anthropic API worker. Membership names do not attest model
identity. Installed versions must support the documented flags; unsupported flags
fail the task rather than falling back to interactive or unrestricted execution.

Claude Code runs in print mode with JSON-schema output, built-in tools disabled,
MCP tools denied and an empty strict MCP configuration. Gemini CLI runs headless
in plan mode with JSON output. Plan mode is the CLI's tool policy, not an OS
sandbox; keep workers under your own authorized local configuration. Both return
structured proposals rather than editing a shared project. JSON envelopes are
normalized and independently schema-validated; errors, truncation and markdown
wrappers do not count as successful answers. No permission-bypass flags are used.

Codex quota classification is unchanged. For the two added CLIs, unrecognized
quota/error responses fail the task; cross-vendor reset timestamps are not guessed.
The result remains visible instead of silently cycling accounts or retrying errors.

## Use an existing LinJun proxy

Use `examples/pool/linjun.toml` for an already-configured local OpenAI-compatible
endpoint. Set the worker's node token and local `LINJUN_API_KEY` environment
variable, and choose an actual model alias from that proxy. Loopback HTTP is
supported for `localhost`, `127.0.0.1` and `::1`; remote endpoints require HTTPS.
Redirects are refused. API keys stay on the worker. This path requires Chat
Completions JSON mode support from the selected proxy/model.

## Attribution and validation

Inspected LinJun README and `src/main/utils/cliDetector.ts` /
`src/main/ipc/cli.handlers.ts` at commit
`ab682f8c2746a70231a6ab506c22f4e806c48f82` on 2026-10-26. All AgentForeman adapter
code is original; no LinJun code or auth-handling logic was copied.

Protocol references:
[Claude Code CLI](https://code.claude.com/docs/en/cli-reference),
[Gemini headless JSON](https://geminicli.com/docs/cli/headless/),
[Gemini plan policy](https://geminicli.com/docs/reference/policy-engine/).
Automated tests use explicitly named CLI envelope fixtures, with real native
process supervision, receipt reuse and schema checks. They do not establish live
Claude/Gemini login compatibility, paid model quality, or real quota reset timing.
