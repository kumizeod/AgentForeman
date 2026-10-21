# Local experiment control panel (v0.6)

The UI is implemented and connected to the durable experiment manager. It reuses
actual MIT-licensed codex-tools themes and layout styles, and ports its classic
navigation, SVG icons and quota-meter component. The account list becomes an
experiment list. See [third-party notices](../NOTICE.md).

## Start on Windows, macOS or Linux

From the repository, activate your existing virtual environment, then:

```powershell
git pull
python -m pip install -e ".[demo]"
python -m agentforeman ui --project .\my-experiment
```

The command opens a local browser automatically. No Node.js, npm, Tauri or frontend
build is needed by the user. macOS/Linux use `./my-experiment` instead. The project
must already contain `foreman.toml`; create the bundled demo using
`python -m agentforeman init ./my-experiment` if you need a new project.

```bash
python -m agentforeman ui --project ./my-experiment --port 8766 --no-open
```

`--no-open` prints the URL without launching a browser. Port 0 chooses a free port.
Each server manages exactly the project passed on startup. To open a different
project, start another server on a different port. It serves only `127.0.0.1`.

**Open the complete URL printed in the terminal**, including its `#...` capability.
The browser stores the capability in per-tab session storage and removes the
fragment from the visible address bar. A fresh browser session opened at the bare
address needs the complete terminal URL again. Keep this URL private. The
capability is new on each server start. It is never written into HTML, HTTP access
logs, disk files or query parameters. No remote hosting/tunnel is configured.

## Pages and real actions

- **实验调度**: search/filter actual campaigns, see stage, verified best metric,
  completed rounds, deadline, controller liveness and quota waiting. Select a task
  for decisions, events, bounded controller/manager/worker log tails and the report.
- **新建夜间任务**: submit goal, next local cutoff time, maximum rounds, optional
  target metric and optional preflight quota check. Browser time is converted to
  an explicit ISO timestamp. A durable campaign is created and an independent
  background controller is launched. The form prevents duplicate clicks and never
  automatically retries submission. On a connection failure, inspect the list
  before submitting again. A startup failure returns the already-created ID.
- **停止后续轮次**: writes the existing durable stop request; it does not abruptly
  terminate training. The controller completes/reconciles current work and starts
  no further rounds. A quota-waiting controller notices the stop promptly.
- **恢复任务**: invokes the existing launch/recovery path. An already-live controller
  is reused. Failed tasks require the explicit "已检查错误，允许重试失败阶段" checkbox.
  Completed campaigns cannot be restarted. A stop request is never silently cleared.
- **下载最佳归档**: downloads the real best verified replay ZIP, after checking the
  recorded SHA-256 and bounding the file path to the selected campaign.
- **额度管理**: manager/worker cached windows, freshness, actual duration, remaining
  percentage and reset time. Only the explicit quota refresh button requests
  read-only metadata. Normal polling, tab navigation and log viewing make no model
  calls or quota RPCs.
- **用量统计**: observed job token counts and reporting coverage; unreported totals
  stay unknown. Download JSON for analysis. This is not account-wide billing.
- **环境设置**: actual configuration/platform/executable checks and a downloadable
  allowlisted diagnostic report. Config editing stays in `foreman.toml`, since
  submitted experiments use frozen configs. The page does not modify credentials.

Light/dark theme is saved locally. The layout adapts to narrow screens. UI strings
are Chinese; raw CLI logs/reports retain their original language. Existing single
runs are still managed through their CLI commands; this page controls campaigns
and overnight tasks. There are no decorative buttons pretending to manage account
pools, proxies or GPU workers.

The panel reads local state every five seconds and pauses polling while hidden.
Quota waiting remains a model-free Python timer in the detached controller.
Closing the browser or stopping the panel server does not stop detached work.
The computer must remain awake; an OS restart still requires manual resumption.

## Implementation and access boundary

`webserver.py` uses the Python standard-library threaded HTTP server; no new
production dependency is needed. JSON APIs call the existing campaign, overnight,
quota and observation modules. They never compose shell command strings.
Packaged assets are served from a fixed allowlist, not arbitrary local paths.

All JSON endpoints require the per-launch capability. Mutation endpoints also
require exact same-origin JSON requests and the correct Host. There is no CORS
permission, remote bind option or user-supplied filesystem path endpoint. Task
text, logs and reports are rendered as escaped text; they are never executed as
HTML/Markdown. CSP disables inline scripts and framing. Logs can contain project
output and should be treated as local research data. This is a single-user local
console, not a multi-user internet service or hostile-code sandbox.

## Attribution and verification

`web/upstream/PROVENANCE.json` identifies every unchanged source stylesheet and
its Git blob hash. The MIT copyright/license accompanies the copied assets in
source distributions and wheels. The original React/Tauri application is not a
runtime requirement; the frontend port uses ordinary browser JavaScript.

API tests cover Host/token/Origin checks, static path restrictions, input validation,
explicit quota refresh, real training submission and archive validation, stop
while waiting, existing-runner resume, explicit failed retries and preserving a
submission after uncertain controller startup. Native platform CI also runs the
full existing engine suite. Browser tests exercise the actual form/navigation,
XSS escaping, search, theme, quota refresh, logs, report and archive download.
Model/planner/quota replies in tests are explicitly labeled fixtures. Real
subscription quota resets and authenticated Codex inference still need live
validation on a configured machine.
