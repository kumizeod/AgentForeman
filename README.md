# AgentForeman

**Give Codex an experiment goal. Manage bounded rounds, independently verify results, keep the best candidate, and resume after exit.**

AgentForeman is an early, local-first experiment manager. Its v0.3 campaign layer
asks a read-only Codex manager to propose an experiment, repair, retest or stop.
The controller validates decisions, enforces round/time limits, and invokes a
separate coding worker. Each run snapshots code, trains supervised processes,
verifies checkpoints independently, and archives evidence. Single-run commands
remain available. The small Python control plane uses psutil for native
Windows/macOS process identity and Windows process-tree cancellation.

[中文上手指南](docs/QUICKSTART.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Campaign manager](docs/CAMPAIGNS.md) · [Recovery](docs/RECOVERY.md) · [Extending experiments](docs/EXTENDING.md) ·
[Roadmap](docs/ROADMAP.md) · [References](docs/REFERENCES.md)

## Status and scope

- Real Codex CLI adapter with JSON event logs and a structured handoff.
- Real CSV files, NumPy MLP optimization, NPZ checkpoints and detached processes.
- External evaluator recomputes predictions from checkpoint tensors; it does not
  import candidate code or accept the worker's claimed score.
- SQLite state, transactional event history, per-run/per-job locks, durable exit
  receipts, process birth identities and restart reconciliation.
- Source snapshots, exact patch, logs, metric comparison, SHA-256 manifest and a
  relocatable replay script in each completed experiment archive.
- Persistent multi-round campaigns: model-proposed decisions, validated action
  references, repair lineage, independent retests, immutable best snapshots,
  explicit round limits and elapsed wall-clock deadlines, including pauses.
- This is a single-machine manager MVP. Distributed scheduling, GPU leases,
  learned policies, token/money budgets and multi-user isolation remain future work.

**Validation disclosure:** the development environment had no Codex CLI or model
authentication available. Tests use an explicitly named **test-only CLI fixture**
for planning and source edits; training, validation, process supervision and archive replay run
for real. A live authenticated Codex run remains to be checked on the user's machine.
No production command silently falls back to a fixture. See `evidence/VALIDATION.md`.

## Requirements

- Native Windows, macOS or Linux (WSL2 is also supported), Python 3.11+, Git recommended.
- Codex CLI installed and authenticated for live editing.
- NumPy for the bundled example. No GPU or data download required for this example.
- A local durable filesystem with native file locking and SQLite; network filesystems
  and multi-host execution are outside this release's support boundary.

## Platform implementation and verification

| Platform | Native implementation | Verified in this delivery |
| --- | --- | --- |
| Linux / WSL2 | flock, /proc birth receipts, detached sessions/process groups | Full local integration suite on Linux |
| macOS | flock, psutil creation time, detached sessions/process groups | Native integration CI passed on Python 3.11/3.12/3.13 |
| Windows PowerShell | msvcrt byte locks, psutil creation time, detached supervisors, known-tree cancellation, npm shim resolution | Native integration CI passed on Python 3.11/3.12/3.13 |

The GitHub workflow runs the complete suite on Ubuntu, Windows and macOS with
Python 3.11/3.12/3.13. All nine combinations passed for the v0.3 implementation
commit `3a83afe`: [CI results](https://github.com/kumizeod/AgentForeman/actions/runs/39427543907).
These tests use explicit planner/editor fixtures; live Codex inference is still pending.
See [platform details](docs/PLATFORMS.md) for exact behavior and limits.

## Quick start

After installing and initializing a project using the platform instructions below,
start a bounded campaign (use your environment's Python on every OS):

```bash
python -m agentforeman campaign start --project ./my-experiment --goal "Improve validation accuracy within the checkpoint contract" --max-rounds 3 --budget-seconds 1800
python -m agentforeman campaign resume CAMPAIGN_ID --project ./my-experiment
python -m agentforeman campaign report CAMPAIGN_ID --project ./my-experiment
```

The manager uses your configured Codex CLI/login in a separate read-only session.
No extra model provider or fallback editor is installed. See [campaign behavior and recovery](docs/CAMPAIGNS.md).

From the extracted source directory, macOS/Linux:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[demo]'
npm install -g @openai/codex
codex login
codex --version

./.venv/bin/python -m agentforeman init ./my-experiment
./.venv/bin/python -m agentforeman doctor --project ./my-experiment
./.venv/bin/python -m agentforeman start --project ./my-experiment \
  --goal "Improve validation accuracy; keep the checkpoint contract and use one coherent candidate."
```

Native Windows PowerShell (no WSL or venv activation required):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[demo]"
npm install -g @openai/codex
codex login
codex --version
.\.venv\Scripts\python.exe -m agentforeman init .\my-experiment
.\.venv\Scripts\python.exe -m agentforeman doctor --project .\my-experiment
.\.venv\Scripts\python.exe -m agentforeman start --project .\my-experiment --goal "Improve validation accuracy with one coherent candidate."
```

In the remaining examples, `python` means that same environment's interpreter;
use `.\.venv\Scripts\python.exe` on Windows or `./.venv/bin/python` on macOS/Linux.
Windows npm `codex.cmd` is resolved to its package's Node entry without a shell.
Custom installations can set `[codex].command` to an explicit executable prefix.

`foreman` is also installed as a console command. The module form works even when
your shell's PATH does not include the console scripts directory.

The command prints a run ID **before** starting execution. Keep it. Codex runs
with `workspace-write` and no interactive approval prompts; actions that cannot
run under that sandbox fail visibly. We do not disable its sandbox automatically.
If the CLI's flags or authentication differ on your installation, inspect
`jobs/codex-00/stderr.log` and update `src/agentforeman/codex.py` deliberately.

You may first inspect the independently measured baseline without calling Codex:

```bash
python -m agentforeman start --project ./my-experiment \
  --goal "Improve validation accuracy" --until codex
```

`--until` pauses **before** the named phase. Resume with:

```bash
python -m agentforeman resume RUN_ID --project ./my-experiment
python -m agentforeman status RUN_ID --project ./my-experiment
python -m agentforeman events RUN_ID --project ./my-experiment
python -m agentforeman report RUN_ID --project ./my-experiment
```

## Interrupt and resume

Ctrl-C stops the **manager**. An active supervised job continues. Resume reattaches
to that job or consumes its existing result; it does not rerun completed phases.
Force-termination (POSIX SIGKILL or Windows TerminateProcess) is handled the same way on resume, although the
last stored status can still read `running` until reconciliation.

To terminate an active command instead:

```bash
python -m agentforeman cancel RUN_ID --project ./my-experiment
```

Failed phases require an explicit retry, after inspecting their logs:

```bash
python -m agentforeman resume RUN_ID --project ./my-experiment --retry-failed
```

Retries are new job attempts. A failed editing attempt is reset to the frozen
baseline before invoking Codex again. Failed training outputs are retained in
`failed_outputs/`; the next training attempt gets an empty artifact directory.
See [recovery semantics](docs/RECOVERY.md), particularly supervisor loss and reboot.

## Results

Completed evidence lives at:

`.foreman/runs/RUN_ID/archive.zip`

```bash
python -m agentforeman verify-archive ./my-experiment/.foreman/runs/RUN_ID/archive.zip
```

After extracting it to a directory:

```bash
python reproduce.py --baseline
python reproduce.py
```

`completed` means the workflow executed and verification passed. `objective_met`
separately records whether the candidate beat the baseline by the configured
delta. A worse candidate is a valid experiment, not a fabricated improvement.

The bundled task is synthetic two-moons classification with one fixed seed. It
validates the engineering loop, not general architecture-search performance.
Its supported checkpoint is a two-layer ReLU MLP; arbitrary architectures require
a corresponding trusted evaluator. Validation data are not a final research test set.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests actually kill managers, race supervisor launches, enforce timeout/cancel,
inject training crashes and unauthorized edits, reject symlinks, ignore fake
scores, detect archive tampering, and reproduce a trained candidate from an
extracted archive. The deterministic editing fixture is in `tests/` only.

## Trust boundary

This is a personal, trusted-project runner, not a hostile-code sandbox. Local
training runs with your user's permissions and inherited environment; source
hash checks detect accidental/prohibited edits but cannot isolate malicious code
running as the same OS user. Use a dedicated container/VM for untrusted projects.
Checksums detect corruption relative to the manifest; they are not signatures.
No auto-merge, publishing, package installation, or deployment is performed.

## License

MIT. This implementation is original; references are acknowledged without
vendoring their code.
