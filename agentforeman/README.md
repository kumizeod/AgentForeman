# AgentForeman

**Give Codex one experiment goal. Get a trained, independently checked, reproducible result. Resume after the manager exits.**

AgentForeman is an early, local-first experiment manager. It snapshots a project,
measures its baseline, asks the real Codex CLI to implement one candidate, runs
training as a supervised process, verifies the checkpoint independently, and
archives the evidence. The control plane uses Python's standard library.

[中文上手指南](docs/QUICKSTART.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Recovery](docs/RECOVERY.md) · [Extending experiments](docs/EXTENDING.md) ·
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
- One candidate per run; a fixed workflow manages execution. There is no learned
  manager, autonomous multi-round search, or multi-agent scheduler in this release.

**Validation disclosure:** the development environment had no Codex CLI or model
authentication available. Tests use an explicitly named **test-only CLI fixture**
for source edits; training, validation, process supervision and archive replay run
for real. A live authenticated Codex run remains to be checked on the user's machine.
No production command silently falls back to a fixture. See `evidence/VALIDATION.md`.

## Requirements

- Linux or WSL2, Python 3.11+, Git recommended; native Windows/macOS are not supported yet.
- Codex CLI installed and authenticated for live editing.
- NumPy for the bundled example. No GPU or data download required for this example.
- A local durable filesystem with working `flock` and SQLite; network filesystems
  and multi-host execution are outside this release's support boundary.

## Quick start

From the extracted source directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[demo]'
npm install -g @openai/codex
codex login
codex --version

python -m agentforeman init ./my-experiment
python -m agentforeman doctor --project ./my-experiment
python -m agentforeman start --project ./my-experiment \
  --goal "Improve validation accuracy; keep the checkpoint contract and use one coherent candidate."
```

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
SIGKILL of the manager is handled the same way on the next resume, although the
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
