# Bring your own experiment

Create a project with sibling `workspace/` and `trusted/` directories and
`foreman.toml`. The bundled initialization is the smallest concrete example.
For an existing repository, copy a focused experiment subtree into `workspace/`
or set `source` to an appropriate directory inside the project. The manager
copies that source into each run. It never modifies your original source.

## Training contract

Configure `[train].command` as an argv array. Available placeholders:

| Placeholder | Value |
| --- | --- |
| `{python}` | Python interpreter running AgentForeman |
| `{workspace}` | Frozen baseline or candidate source directory |
| `{artifacts}` | Separate directory for this attempt's model outputs |
| `{trusted}` | Run snapshot of trusted evaluator/data |

Training must finish with exit code zero and write a real artifact that your
verifier understands. Configuration/checkpoint metadata should be saved alongside
the model. The runner is framework-agnostic; a PyTorch command can select CUDA
on your 5090. Install its dependencies in the environment before starting.

Example command for a project you supply (the PyTorch trainer is not bundled):

```toml
[train]
command = ["{python}", "{workspace}/train.py", "--device", "cuda", "--output", "{artifacts}"]
timeout_seconds = 3600
```

## Verification contract

Configure `[verify].command` to run trusted evaluator code outside the candidate
directory. It must not import candidate code merely to accept its claimed score.
For arbitrary architectures, design an explicit model/serialization protocol
and account for the security implications of loading model artifacts.

It must exit zero and produce `{artifacts}/verification.json`:

```json
{
  "valid": true,
  "metric_name": "accuracy",
  "metric": 0.91,
  "checkpoint_sha256": "optional evidence",
  "validation_rows": 600
}
```

The metric name must equal `[metric].name`; the numeric metric must be finite
and within `[metric].minimum` / `maximum`. A false validity flag, failed evaluator
exit, missing result or changed trusted/source hashes fails the workflow.

The evaluator owns data checks, checkpoint shape/format checks and recomputation.
The manager's generic protocol cannot guarantee that a user-supplied evaluator
is scientifically sound. Fix the validation split before the campaign, and keep
a separate final test set for any research claim.

## Editable scope

`allowed_edit` contains exact relative filenames, not globs. Files must already
exist. Unlisted modifications, additions, deletions and symlinks are rejected.
Ignored cache/VCS directories are not part of source manifests. Put only relevant
code/data in the source subtree: copied files may become part of the archive.
The current textual patch generator expects editable files to be UTF-8 text.

## Codex launch prefixes

`[codex].executable` defaults to `codex` on PATH. Native binaries are launched
directly. A Windows npm `.cmd` shim is resolved through the adjacent
`node_modules/@openai/codex/package.json` bin entry and launched with Node,
without passing the goal or paths through cmd.exe. For nonstandard installations:

```toml
[codex]
command = ["node", "C:/custom/node_modules/@openai/codex/bin/codex.js"]
timeout_seconds = 600
```

`command` overrides `executable` and must be an argv array. Use forward slashes
or TOML literal strings for Windows paths; unescaped backslashes in a double-
quoted TOML string are escape characters. Python `.py` and Node `.js` entry paths
are also supported explicitly; no production fixture is selected automatically.

## Future adapters

The first extraction points for further development are `codex.make_spec`,
`job.wait_for_job` and `engine.drive`. A later worker protocol can add an SDK or
app-server adapter; a runner protocol can add container/remote execution. Keep
these interfaces tied to recorded attempts and independent verification.
