# Quick start and local connections

**v0.13 update:** see [Antigravity, immediate termination and independent accounts](V0_13.md). Google AI Pro/Ultra uses Antigravity CLI, not the legacy Gemini CLI.

## 中文快速上手

1. Windows 更新仓库后双击根目录的 `start-windows.cmd`；首次需要安装 Python 3.11+。后续不要关闭启动窗口。
2. 浏览器默认进入「任务」。切换「订阅与模型」→「添加连接」，选择 Codex、Antigravity、Claude Code、OpenCode 或 API。已有本机登录可以直接添加；未登录可点击「打开官方登录终端」。API 填服务商提供的真实地址、密钥和模型名。
3. 点击「添加并测试连接」，通过后自动加入本机调度。回首页点击「新建任务」，输入任务，与主席澄清需求，核对目标、角色和验收方法，再点击确认开工。

对抗模式至少需要三个连接，合作模式至少需要四个。相同账号的不同模型可以分别添加，但会串行使用同一资源，不能算作多份额度或模型独立性的证据。暂时没有提供自动安装所有 CLI、跨电脑邀请链接、订阅令牌集中托管或运行中静默换人的功能。

关闭浏览器不影响执行；电脑和启动窗口需要保持运行。默认 API 密钥只在本次进程内有效，可选择系统凭据库保存。中转站是否可用以真实测试为准。下文给出了认证、恢复和验证的完整边界。


Open **订阅与模型** in the local console and click **添加连接**. Add a connection, wait for its real test to pass, describe a task, review the chairman's contract and role allocation, then confirm that exact version. No manual worker registration, token creation, coordinator command, or TOML editing is needed for these local connections.

## Windows

With Python 3.11+ installed, update/download the repository and double-click `start-windows.cmd`. It creates/reuses `.venv`, installs `.[demo,desktop]`, reuses `my-experiment` if configured, otherwise initializes the offline example in a new/empty directory, and opens the console on an available localhost port. It never overwrites a nonempty unconfigured project. Keep the terminal and computer running. The launcher has no administrator requirement; its execution-policy override applies to that invocation only. An alternative project can be supplied to `scripts/start-windows.ps1 -ProjectPath ...`.

On macOS/Linux, or from an existing Windows environment:

```sh
python -m pip install -e '.[demo,desktop]'
# Only for a NEW project; do not initialize over existing work:
python -m agentforeman init my-experiment
python -m agentforeman ui --project my-experiment --port 0
```

The example project supplies the existing console configuration. General artifact tasks do not execute that example's trainer. Installing Python and the desired official CLI clients remains a prerequisite; this release is not a bundled desktop executable.

## Connection types

| Connection | Authentication | Model setting |
| --- | --- | --- |
| Codex / ChatGPT | Official local Codex login (`codex login`) | Optional CLI model |
| Antigravity / Google AI Pro | Official Antigravity login (`agy`) | Optional CLI model |
| Gemini CLI (legacy enterprise/API) | Existing supported enterprise/API login (`gemini`) | Optional CLI model |
| Claude Code | Official local Claude Code login (`claude auth login`) | Optional CLI model |
| OpenCode / Go | OpenCode's own authentication (`opencode auth login`) | Optional `provider/model`; otherwise its local default |
| OpenAI-compatible API / relay | API key or existing environment variable | Explicit service model; Chat Completions |
| Anthropic-compatible API / relay | API key or existing environment variable | Explicit service model; Messages |

The **打开官方登录终端** button launches only the selected installed official client's fixed login command. Windows uses a new native console and argv (including npm shim resolution), macOS opens Terminal, Linux uses an installed desktop terminal. No terminal available: the UI gives the command to run yourself. The UI never reads, uploads, or exchanges subscription OAuth tokens. By default, model connections to the same CLI use the current local login. v0.13 adds optional independent official-client profiles for Codex and Claude Code; see V0_13.md.

OAuth UX belongs to each client. Do not treat subscription OAuth as an API key or promise all subscriptions work in third-party harnesses. Subscription entitlements and API billing are separate. Claude integration retains the original client and user authentication; API is the appropriate direct third-party integration path. Follow each provider's current terms and permissions.

For Cubence or another relay, use its actual base URL, advertised model name and compatible protocol. No service-specific model names, login endpoints or subscription guarantees are invented. Remote API origins require HTTPS; loopback HTTP is accepted for local proxies. Credentials are never forwarded through redirects. Responses API and arbitrary custom authentication headers are not implemented in this release.

## Secret storage

- API keys are session-only by default. They are placed in a private, generated environment-variable name, not in connection JSON, job specs, reports or API responses. Closing/restarting the process loses these keys; the cards identify missing keys, and **更新密钥** replaces a key and requires a fresh test.
- Optional **保存到系统凭据库** uses native Windows Credential Manager, macOS Keychain, or Linux Secret Service through `keyring`. Install the `desktop` extra. Unsupported, missing or locked backends fail explicitly; there is no plaintext credential-file fallback.
- An existing environment-variable name is also accepted. It must be present in the process starting the console.
- Session credentials are available to local child processes through their inherited environment; this is local orchestration, not isolation against hostile local code.

The existing loopback-only console requires its random capability, exact Host, and same-origin JSON for all mutations. Do not expose that console to the network.

## Automatic local runtime

Opening the connection center starts an ephemeral-port local coordinator and one managed worker per enabled, tested connection. Saved enabled connections automatically resume when the console is restarted. Worker identity, job receipts, lease/outbox state and connection configuration persist under the project. A project-wide service lock rejects a second console owner instead of starting duplicate workers.

The console opens on **快速开始** by default. Closing the browser is safe. Stopping the console stops local scheduling; a forced process exit is recovered conservatively through existing native receipts and leases. This is not an installed OS background service. External registered workers and `pool serve` continue to be available through the advanced workflow; the quick-task selector currently uses local managed connections only.

Tests run one small, bounded model request through a private HTTP pool and verify the exact result of 17 + 25. A passed test enables/registers the worker automatically; installation detection alone never does. Tests can consume a small amount of subscription/API allowance and do not prove multi-model quality. A busy resource is reported for retry, rather than launching a concurrent call. The UI does not poll a model just to refresh status.

All connections sharing one local CLI login execute serially, regardless of selected model. API connections with the same base URL are conservatively serialized too. Quota pauses propagate between those local resource connections. Known Codex reset timestamps and API Retry-After are honored; other CLI providers require explicit structured quota errors. Unknown generic CLI reset times use a 30-minute fallback; unrecognized failures remain failures. The system does not infer precise remaining subscription allowance or money spent.

## Chairman allocation and confirmation

The simple task form automatically selects the preferred chairman, supplies a bounded roster of at most 12 available tested connections, and asks it to choose execution mode, members, independent reviewers and a rationale during clarification. Cooperation requires at least four distinct connection identities; adversarial execution requires three. Multiple connections to one model/account are allowed but explicitly share resource capacity and are not evidence of model independence.

The chairman can change the role proposal during clarification. The controller validates registered identities, fixed chairman, role separation, mode limits, and existing execution budgets. It rejects invented nodes and duplicate identities. Model output cannot add budget, shell commands or checks. The chosen roles, mode, budgets, evidence and checks are frozen in the human-confirmed version. Before confirmation, only the chairman receives tasks. Without configured automated checks, acceptance remains manual/needs-evidence; model agreement cannot mark factual correctness verified.

Default execution limits are eight hours, three review rounds and ten minutes per stage. These are time/iteration limits, not a monetary API budget. Review/edit the execution settings before confirmation when needed.

Connections can be added, tested and paused at any time for subsequent task planning. This version does **not** silently replace a frozen role with a newly added connection in an existing run. In-flight calls are not killed when a connection is paused. Use v0.13’s explicit task-level immediate termination button to interrupt them. Automatic mid-run roster migration, remote member invitation links and native installed background service are future work. Existing multi-agent tasks preserve their strict contract/recovery semantics.

## Validation

`tests/test_connections.py` exercises actual localhost API requests, subprocess supervision, onboarding, key redaction/storage failure, restart, quota sharing, connection pause and both complete execution modes with frozen independent checks. `tests/browser/ui.cjs` exercises the forms and confirmation journey. Provider replies in these tests are explicitly scripted. They do not certify a real ChatGPT, Google, OpenCode Go or relay subscription. Authenticate and test each real connection on the deployment computer.

References: [OpenCode CLI](https://opencode.ai/docs/cli/), [OpenCode permissions](https://opencode.ai/docs/permissions/), [Gemini authentication](https://geminicli.com/docs/get-started/authentication/), [Claude Code integration conditions](https://code.claude.com/docs/en/legal-and-compliance), [Claude account authentication](https://support.claude.com/en/articles/13189465-log-in-to-your-claude-account).

## 删除模型连接（v0.14.2）

在「订阅与模型」每条连接右侧点击「删除」，核对名称后确认。删除的是 AgentForeman 的接入连接，不是取消订阅、卸载客户端或退出官方账号。独立账号配置仍可在以后添加连接时复用。

- 空闲连接立即移出列表和自动分工；本应用专门保存的此连接 API 密钥会从本次进程或系统凭据库移除。用户自己的环境变量、其他连接引用的变量及官方客户端登录目录保持原样。
- 连接正在测试，或仍有运行中 / 不确定的 lease 时，先暂停新任务领取并阻止删除。请在任务页终止相关任务，等待退出确认，再删除。没有本机可靠退出回执时不能用删除操作宣布调用已停。
- 已有任务、原生回执、产物、日志与 token 用量不删除。保留不含密钥的连接墓碑，用于历史用量中的名称和账号连接关联；删除不会把旧 token 归到新模型。
- 已确认的任务不会自动改用其他模型，未完成的任务可能需要终止后重新安排。不会因删除一个模型而静默放宽独立角色要求。
- 已保存的分配偏好会移除该连接，并按比例归一化其余非零份额，保留显式 0%。如果剩余模型原本全是 0%，请重新设置偏好；删除最后一个连接后，新加连接恢复默认占比。

删除接口继续使用本机 capability、Host 和 Origin 保护，无强制绕过退出确认的选项。重复删除同一连接不重复删除历史或改变其他账号。
