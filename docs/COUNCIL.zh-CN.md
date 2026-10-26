# 小组夜间委员会：第一次使用

目标：成员各自在自己的机器上运行 worker，统一领取小组任务；先独立回答，
再匿名互评，由单独的 Claude API 节点综合，最后由另一位成员审查。
账号额度仍各自计量，系统统一调度任务；Codex 登录凭据留在成员自己的机器上。
额度耗尽后该节点等待，尚未完成的任务可以交给其他合格节点。

## 1. 所有人更新

在仓库目录、已有虚拟环境中运行：

```powershell
git pull
python -m pip install -e ".[demo]"
```

下面沿用已有的 `my-experiment`。没有项目时可先运行
`python -m agentforeman init my-experiment`，已有目录不必重新初始化。

## 2. 选一台协调器，注册成员

在协调器上，为每位成员生成独立的节点令牌并注册，例如：

```powershell
$env:MEMBER_A_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m agentforeman pool register --project .\my-experiment --member member-a --token-env MEMBER_A_TOKEN
```

再用另一份令牌注册 `member-b`，用第三份注册 `claude-chair`。令牌通过小组
自己的安全渠道分发给对应节点；不要提交到 GitHub。这是 worker 访问协调器的
令牌，不是 Codex 账号密码，也不是 Claude API key。协调器只保存其哈希。

启动协调器并保持终端运行：

```powershell
python -m agentforeman pool serve --project .\my-experiment --port 8770
```

另开终端查看节点和任务：

```powershell
python -m agentforeman pool status --project .\my-experiment
```

## 3. 成员运行自己的 worker

每位成员先在自己的电脑上完成 `codex login`。复制
`examples/pool/member.toml` 到本地配置文件，填写协调器地址、独立的状态目录、
可选模型和夜间可用时间。默认 `22:00-08:00` 按成员机器的本地时钟计算。

在成员自己的终端设置管理员发给自己的 `FOREMAN_WORKER_TOKEN`，然后运行：

```powershell
python -m agentforeman pool worker --config .\member.toml
```

跨电脑连接可以通过 HTTPS 入口，或把协调器的回环端口用 SSH 转发到本机：

```text
ssh -N -L 8770:127.0.0.1:8770 user@coordinator-host
```

保持转发终端运行，worker 配置使用 `http://127.0.0.1:8770`。
远程明文 HTTP 地址不会被 worker 接受。协调器上的实验网页仍只供本机使用。

macOS/Linux 使用相同 Python 命令，把环境变量设置方式换成 `export`、路径换成
系统对应格式即可。Windows npm 命令入口的处理沿用已有跨平台适配。
每个成员身份只运行一个状态目录，避免同一身份在不同电脑重复执行。

## 4. 运行 Claude 主席节点

复制 `examples/pool/chair.toml`，把其中的模型占位符替换为你们 API 账户可用的
Claude 模型 ID。可以选你们最强的模型；系统不会猜测账号权限。

主席节点本机设置两项环境变量：

- `FOREMAN_CHAIR_TOKEN`：注册 `claude-chair` 时对应的节点令牌。
- `ANTHROPIC_API_KEY`：你们自己的 Anthropic API key。

然后运行：

```powershell
python -m agentforeman pool worker --config .\chair.toml
```

API key 不写入 TOML，不发送给协调器。其他兼容 Chat Completions JSON 模式的
API 也可以作为 worker，详见 [配置说明](WORKER_POOL.md)。订阅额度与 API
费用分别计量；输出 token 上限和超时不是费用总额上限。

## 5. 网页提交一个问题

在协调器另开终端：

```powershell
python -m agentforeman ui --project .\my-experiment
```

打开“共享委员会”：

1. 确认至少两位探索成员和一个独立主席节点已注册、连接。
2. 写清研究目标、固定约束和希望检验的问题。
3. 选择探索成员，选择另一个主席节点。
4. 有文献证据时填入来源目录 JSON；缺少证据会明确标为待补证据。
5. 如果成员还没到夜间可用时间，填写“延后开始”，避免第一阶段提前超时。
6. 选择是否额外运行本项目的一次验证实验，再提交。

默认完成后会得到：独立答案、匿名评议、主席答案、独立审查、待验证问题和
可导出的完整 JSON。最大阶段等待时间防止长期等待离线成员。

也可以从 CLI 提交：

```powershell
python -m agentforeman council start --project .\my-experiment --question "在固定训练预算下，哪一个模型修改最值得先验证？请给出对照实验和失败标准。" --members member-a member-b --chair claude-chair --start-at 22:00
```

`22:00` 表示本机下一次到达该时间；如果现在就要开始，去掉 `--start-at`。

## 6. 可选：让建议进入真实实验

勾选网页的实验选项，或 CLI 增加 `--experiment`。委员会结束后，协调器会针对
第一条实验建议建立一个本地 campaign，默认额外预算一小时、最多一轮。
协调器必须已有可用的 Codex、训练命令和独立验证命令。工作区或配置在提交后
发生变化会阻止自动衔接；不会直接照搬远端模型给出的 shell 命令。

报告会附上对应实验 ID、基线/最佳指标和校验过的归档哈希。实验可以在原来的
“实验调度”页查看、停止和恢复。远程 worker 当前承担回答与审查，GPU 训练仍
在配置好的本地项目执行。

## 7. 如何验收第一晚

先用一个目标明确、能独立检查答案的小问题，接两位成员和 Claude 主席：

- 两份初稿确实来自不同成员，互相看不到初稿。
- 互评没有评价自己的答案；重要批评在主席输出中都有回应。
- 暂停或重启一个 worker 后，没有重复创建已完成的模型任务。
- 遇到额度等待时，不再持续调用该账号；有其他合格节点时能接手。
- 报告保留来源不足、尚未解决的问题，以及没有执行过的实验。

模型数量、投票和审查通过都不能保证事实正确或论文创新。当前的
`completed_candidate` 表示候选答案通过结构检查和模型审查；
`needs_evidence` 表示还需要证据。真实实验只验证配置好的项目指标，不能直接
证明广泛科研结论。

本仓库的自动化测试使用明确标注的模型样例及 API mock，真实执行 HTTP 分发、
SQLite 租约、进程监督、训练、独立验证、恢复和归档。小组真实账号、付费 Claude
回复质量、真实整夜运行和额度重置，需要你们按上述流程在本机验收。

## 8. v0.9：让不同 coding agent 参与代码实现

现在除了 Codex，也可以使用 Claude Code 和 Gemini CLI。每个成员仍保留自己的
登录和独立 worker 令牌。先在对应成员的机器上检查工具是否存在：

```powershell
python -m agentforeman pool agents
```

这只检查可执行文件，不会读取账号文件，也不调用模型。对应配置模板：

- Codex：`examples/pool/member.toml`。
- Claude Code：`examples/pool/claude-code.toml`。
- Gemini CLI：`examples/pool/gemini-cli.toml`。
- 已有的本地 LinJun 代理：`examples/pool/linjun.toml`。

成员在自己的机器完成对应工具的登录，设置各自的节点令牌，再运行同一个
`pool worker --config ...` 命令。LinJun 代理入口使用其本地 API key 和实际模型
别名，不需要把账号登录文件上传到 AgentForeman。不同进程使用不同成员身份。

委员会完成后，网页点击“让多个 agent 实现当前委员会的方案”，会使用该
委员会的成员发起一轮代码候选实验，额外默认预算一小时。也可以直接运行：

```powershell
python -m agentforeman coding start --project .\my-experiment --goal "在现有验证协议下改进模型，保留可复现结果" --members member-codex member-claude member-gemini --budget-seconds 3600 --proposal-seconds 600
python -m agentforeman coding list --project .\my-experiment
python -m agentforeman coding report CODING_ID --project .\my-experiment
```

`--members` 后填写实际注册的成员 ID；不一定要使用上面的名称。把 `--goal`
换成 `--council COUNCIL_ID`，就会沿用委员会的问题和第一条实验建议。

流程是：冻结项目 → 独立测基线 → 多 agent 分别提交修改文件 → 检查原始代码
指纹与可编辑范围 → 各自在独立快照里训练、验证 → 保留最佳归档。成员可以
并行产生候选，本机逐个评测，原项目源码不会被覆盖。网页可查看结果、停止
后续候选并下载最佳 ZIP。没有加入人工审批，也不会自动合并不同候选的冲突。

当前 coding agent 在受限模式下生成结构化代码修改，控制器负责实际落盘和
训练。这已经能比较不同 agent 的实现，但尚未提供多个 agent 共享终端、
共同编辑一个分支或远程 GPU 集群执行。可编辑配置也可能改变训练预算，不能
仅凭指标提高就声称验证了网络创新。详细边界见 [多 agent 文档](MULTI_AGENT.md)。

Codex 的额度等待沿用原有策略；新 CLI 无法识别的限额或错误会明确记为失败，
不会猜测其重置时间。Claude/Gemini 的真实登录、权限和额度恢复仍需本机联调。
