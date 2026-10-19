# Architecture

## Execution flow

1. `create_run`: validate TOML; snapshot source, trusted data/evaluator and config;
   commit run document and event in one SQLite transaction.
2. `baseline_train`: run the original source through the detached supervisor.
3. `baseline_verify`: trusted evaluator recomputes the metric from actual weights.
4. `codex`: call real `codex exec` in an editable source copy. Persist prompt,
   schema, JSONL stdout, stderr and a structured answer.
5. `check_edits`: reject changes outside the exact allowlist, deletions, symlinks
   and no-op workers. Freeze candidate source; generate a unified diff.
6. `train`: supervise training against the frozen candidate snapshot.
7. `verify`: run the external evaluator; check finite/bounded metric and code hashes.
8. `archive`: compare to baseline; write report, manifest, replay script and ZIP.
9. `done`: commit completion. Resume of a completed run is a no-op.

## Process separation

The manager writes a job spec and starts a detached supervisor. The supervisor
holds a job `flock`, persists its process birth identity, and starts a child shim
in another session. The shim records its own identity, then `exec`s the command,
preserving that birth identity. This avoids PID guesses across process/runtime
namespaces. The supervisor monitors timeout/cancellation and writes an atomic
exit receipt. It does not depend on the manager's stdout or lifetime.

Training stdout/stderr go directly to files. There are no shell command strings,
shell expansion or OS-level shell interpolation of user goals. A user's explicit
command array can of course invoke a shell, but the default configuration does not.

## State and evidence

SQLite stores one run document plus an append-only event table. Each stage
transition commits state and event together. Only one manager may drive a run;
status readers remain concurrent under SQLite WAL.

Job specs/results and reports use temp file + fsync + atomic rename. Directory
fsync is used for JSON/text receipt writes. The supervisor lock is the authority
against duplicate launch races; result files are the authority for exit status.

If the manager dies after a job completes but before committing its phase, resume
consumes the same receipt and continues. If it dies while building a candidate
snapshot or ZIP, that local, repeatable stage is rebuilt from recorded inputs.

The archive's `events.json` is a snapshot through the start of archiving; the
database also contains subsequent phase/run completion events. Historical job
specs contain absolute paths; `reproduce.py` uses relocatable template commands.

## Integrity versus isolation

The worker is asked to edit only exact allowlisted paths, and the manager checks
the complete non-cache source manifest afterward. Evaluation code and validation
data are outside the Codex workdir. Baseline/trusted/config hashes are checked at
stage boundaries; candidate code/data hashes are checked after execution.

These are integrity checks, not a security boundary between same-user processes.
Training code and a compromised worker could access files/environment available
to the OS user. This release is for trusted personal projects. Container/VM
isolation, credential brokerage, artifact signing and quotas are future work.

## Module map

| Module | Responsibility |
| --- | --- |
| `cli.py` | User commands, project initialization |
| `engine.py` | Configuration, state machine, snapshots, policies |
| `codex.py` | Headless Codex CLI argv/prompt/schema/handoff |
| `job.py` | Detached supervisor, process identities, timeout/cancel, receipts |
| `store.py` | SQLite state/events, exclusive run ownership |
| `files.py` | Atomic metadata, snapshots, hashes, argv placeholders |
| `report.py` | Comparison, portable archive, checksum verification/replay |

No agent framework, task broker or web server is required for this milestone.
