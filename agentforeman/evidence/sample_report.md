# Experiment fd2ff51f2df4

Goal: TEST FIXTURE editing; actual training, independent verification and archive replay

| Independent measurement | accuracy |
| --- | ---: |
| Baseline | 0.660000 |
| Candidate | 0.975000 |
| Direction-adjusted improvement | 0.315000 |

Objective: **met** (required delta: 0.02).
Successful execution/verification does not imply the candidate improved.
This is one candidate and one seed; it is not statistical evidence of a research advance.

Worker hypothesis: Fixture increases optimization budget/width to exercise the real pipeline

Worker summary: TEST FIXTURE ONLY: deterministic edit, no LLM call

Changed files: config.json

## Evidence

- `changes.patch`: exact candidate changes.
- `baseline/code`, `candidate/code`: source and input snapshots.
- `*/artifacts`: actual checkpoints, training logs and external verification reports.
- `jobs/*`: argv specifications, process identities, stdout/stderr, exit receipts.
- `worker`: goal prompt, response schema, structured answer.
- `manifest.json`: SHA-256 checksums for the archive contents (not a signature).

## Reproduce

Install the project's training dependencies. For the bundled example: `python -m pip install numpy`.
Run `python reproduce.py --baseline` and `python reproduce.py` after extracting the archive.
Only execute archives from sources you trust. Training code runs with your account permissions.
