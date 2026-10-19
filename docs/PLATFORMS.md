# Native platform support (v0.2.0)

AgentForeman's CLI, project format, SQLite state and experiment protocol are
shared across Windows, macOS and Linux. WSL2 uses the Linux implementation.

| Concern | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Process identity | psutil native PID + creation time | psutil native PID + creation time | /proc birth ticks + boot identity |
| Manager/job lock | msvcrt nonblocking byte lock | flock | flock |
| Detached supervisor | CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS | new POSIX session | new POSIX session |
| Training/worker launch | direct native Popen, hidden console | child shim + exec in new session | child shim + exec in new session |
| Cancellation | psutil snapshot of descendants + root, terminate then kill | process-group TERM then KILL | process-group TERM then KILL |
| File receipt durability | fsync file + replace, sharing-violation retry | fsync file + replace + directory fsync where supported | fsync file + replace + directory fsync |
| Codex npm entry | resolve cmd shim to package bin + Node | normal CLI executable | normal CLI executable |

## Supported use

Use Python 3.11+ and a local writable filesystem. Install Codex CLI separately
and complete its login and native sandbox setup normally. The manager uses the
CLI; having only a GUI application does not guarantee a `codex` command is on
PATH. `doctor` reports its resolved command without calling a model.

Windows PowerShell instructions are in README and the Chinese quickstart. They
invoke the venv interpreter directly, so no activation script or execution-policy
change is necessary. Configuration command arrays preserve spaces and Unicode
paths. All child Python jobs set UTF-8 mode. Tests exercise whole projects under
paths containing both spaces and Chinese characters.

## Recovery boundaries

Ctrl-C or force-terminating the manager leaves the detached supervisor running
when the host permits independent background processes. A supervisor receipt is
reattached or consumed on resume; a lost supervisor is not silently replayed.
Sleep, shutdown, host policy, terminal hosts that kill whole job containers, or
container teardown can still suspend/terminate all work. The manager cannot
override those OS/host policies.

Windows cancellation uses a known descendant-tree snapshot, not Windows Job
Objects. Rapidly spawning/escaping descendants are not guaranteed to be contained.
POSIX process groups likewise do not contain commands that deliberately create
their own sessions. This is a trusted personal runner, not a hostile-code sandbox.

File locks are kernel-owned and released on process exit. Windows metadata
writers retry brief sharing violations; directory fsync is not exposed on Windows
by this implementation. The supported recovery contract covers manager/process
interruption, not a universal power-loss guarantee across every filesystem.

## Validation status

- Linux: complete local training/verification/recovery/archive tests and wheel smoke test.
- Windows/macOS: native branches plus local mocked contract tests for platform
  selection, birth identity, locking, creation flags, cancellation and npm entry resolution.
- GitHub Actions: full suite configured for ubuntu-latest, windows-latest and
  macos-latest with Python 3.11, 3.12 and 3.13. This delivery does not include a
  completed remote CI run. Upload the project to GitHub to obtain those results.
- Live authenticated Codex inference remains pending on all three platforms.

Mocks verify branch contracts; they are not a substitute for executing a native
runner. Evidence files label this distinction explicitly.
