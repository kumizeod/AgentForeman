# Validation disclosure

Recorded on 2026-10-19 in a Linux/Python 3.12 environment with NumPy available.

## What the recorded run proves

`sample_archive.zip`, `sample_report.json`, `sample_summary.json` and
`sample_report.md` come from a real local run. Its editor was
`tests/fixture_codex.py`, an explicitly identified deterministic **test fixture**.
It does not call a model. The run does perform actual gradient descent on CSV
files, save NPZ weights, recompute validation predictions in a separate verifier
process and create a portable archive.

This example's score change is produced by a predetermined fixture increasing
optimization budget and width. It is engineering evidence, not evidence of an
agent independently discovering a better architecture.

## Automated verification

The v0.2.0 local suite contains 38 tests (including 12 platform contract tests).
All were executed on Linux; Windows/macOS contract mocks are labeled as such.

`test-results.txt` contains the final unittest run. It covers normal execution,
manager SIGKILL while training survives, checkpoint/verifier rejection, restart
receipt reuse, concurrent run/supervisor ownership, CLI failure/retry, missing CLI
installation/retry, timeout, cancellation, protected edits, no-op edits, symlinks,
fake scores, archive corruption and replay after extracting the archive.

`package-check.txt` records building the distributable wheel and loading its
bundled templates from a clean installation target.

Reproduce the local evidence without Codex (test workflow only):

```bash
python -m unittest discover -s tests -v
python -m tests.record_evidence --output ./evidence-new
```

## Not verified here

- A live, authenticated `codex exec` inference run. This environment has no
  Codex CLI installed and no model account was configured for this project.
- GPU execution, PyTorch integration or 5090 performance.
- Native Windows/macOS execution: platform branches are implemented and their
  contract tests run here, but no Windows/macOS runner is available in this
  environment. The 3-OS x 3-Python GitHub CI matrix is configured and not remotely executed here.
- Remote/multi-host execution and multi-user isolation.
- Multi-seed scientific improvements or a multi-round autonomous manager.

The real adapter follows the cited official headless interface; validate CLI
version, sandbox behavior and model access on your own Windows/macOS/Linux installation.
No test fixture is selected by a production CLI fallback.
