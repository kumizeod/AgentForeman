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

## Independent implementations and real verification

After a council reaches a non-rejected, protocol-valid result, its panel now has
**让多个 agent 实现当前委员会的方案**. The selected council's members receive
separate code-proposal tasks, over identical frozen project inputs. Existing
`pool serve` advances the implementation round automatically; keep each member's
worker running. API workers may propose code through the same JSON contract.

Or submit directly, without a prior council:

```powershell
python -m agentforeman coding start --project .\my-experiment --goal "Improve validation accuracy under a fixed evaluation contract" --members member-codex member-claude --budget-seconds 3600 --proposal-seconds 600
python -m agentforeman coding list --project .\my-experiment
python -m agentforeman coding status CODING_ID --project .\my-experiment
python -m agentforeman coding report CODING_ID --project .\my-experiment
```

To use the council's question and first experiment, replace `--goal ...` with
`--council COUNCIL_ID`. `coding resume CODING_ID` manually advances a durable stage
if the pool server is absent; it is not needed when that coordinator is running.

The pipeline:

1. Freeze the project's source, trusted verifier/data and configuration. Independently
   train/verify a baseline and archive it before spending agent calls on candidates.
2. Send each agent the goal, metric, editable text files and contract, with a common
   base SHA-256. Agents return complete replacement text for changed allowlisted
   files. They do not receive the trusted dataset/verifier contents and do not run
   training on remote machines. Limit: 2–8 members, 160,000 characters per editable
   file and a bounded combined payload.
3. Collect proposals from distinct eligible members. Proposals may run concurrently
   through the worker pool; after the proposal wait, retain any completed results.
4. Reject stale base hashes, protected/duplicate file paths and no-op proposals.
   Materialize each accepted candidate in its own snapshot. There is no shared
   write directory or automatic merge between independent alternatives.
5. Independently train and verify candidates with the existing `evaluate` pipeline,
   under the same frozen configuration. Only externally verified metrics can update
   the best candidate; a failed/worse candidate leaves the baseline or earlier best.
   Candidate evaluations are sequential within a round; at most two rounds are
   advanced concurrently by one coordinator process. These are not GPU resource leases.
6. Keep reports, candidate provenance, source hashes and replayable ZIP archives.
   The original project source is not overwritten. The panel displays each member's
   candidate and metric and downloads the hash-checked best archive.

The one-hour coding budget is **additional** to the council's research budget.
The proposal wait uses at most half that budget; baseline/evaluation work also counts
against it. Stop prevents subsequent work and queued claims; already-running work
remains bounded by its deadline. Model-quota handling comes from the existing pool.
Native evaluation failure is recorded, never automatically retried ambiguously.

Deterministic evaluation run IDs and native receipts recover a crash after training
but before the controller records the result. Successful work is not rerun on resume.
Changing frozen inputs or the best archive blocks normal progression. A best metric
validates this project's configured protocol, not general research novelty or truth.
The model may propose changes to an allowed config (including optimization settings);
there is no claim that this automatically enforces matched-compute experimental design.

This version provides independent implementations and verified comparison, rather
than autonomous agents sharing an unrestricted shell or merging conflicting changes.
The local trainer executes allowed candidate code under the existing project's trust
model; an OS/container isolation service is not added by this release.
