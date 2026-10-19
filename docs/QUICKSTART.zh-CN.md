# 中文上手指南

这份代码实现的是一个可恢复的单次实验闭环：

**提交目标 → 训练基线 → Codex 修改 → 检查修改范围 → 训练候选 → 独立验证 → 归档。**

它已经接入真实 Codex CLI，不在生产命令里使用脚本冒充模型。但交付环境没有
Codex CLI/登录，真实模型调用尚未现场验证。测试里使用明确标注的 CLI fixture；
训练、CSV、checkpoint、评测和进程恢复均真实执行。

## 1. 在你的机器上安装

支持 Windows 原生 PowerShell、macOS 和 Linux，也可使用 WSL2。这个示例先用
CPU 和 NumPy，不依赖 CUDA，也不下载数据。以后换 PyTorch 训练命令即可使用 GPU。
当前环境只实测了 Linux；Windows/macOS 分支有契约测试，并配置了三平台完整 CI，
原生测试结果要以对应系统执行后的记录为准，不能把分支测试当作原生实测。

解压项目，在含有 `pyproject.toml` 的目录执行：

macOS / Linux：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[demo]'
npm install -g @openai/codex
codex login
codex --version
```

Windows PowerShell（不需要 WSL，也不需要修改 PowerShell 执行策略）：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[demo]"
npm install -g @openai/codex
codex login
codex --version
```

下文的 `python` 都指安装项目的虚拟环境解释器：Windows 替换为
`.\.venv\Scripts\python.exe`，macOS/Linux 替换为 `./.venv/bin/python`。
所有后续 CLI 参数在三种系统中一致。含空格或中文的路径请加引号。
多行命令中的反斜杠是 Bash 语法；PowerShell 请写成一行或使用反引号续行。

Windows npm 安装产生的 `codex.cmd` 会自动解析到其 Node 程序；自定义安装位置若
无法解析，可在 `[codex]` 中设置 `command = ["C:/路径/codex.exe"]`，或
`command = ["node", "C:/路径/@openai/codex/bin/codex.js"]`。它是程序参数数组。

CLI 登录按你的 Codex 账号和官方支持方式完成。模型访问、额度、订阅与 API
计费取决于你采用的认证方式；本项目不会自动购买额度或切换到付费认证。

## 2. 创建真实示例

```bash
python -m agentforeman init ./my-experiment
python -m agentforeman doctor --project ./my-experiment
```

生成的文件：

| 文件 | 作用 |
| --- | --- |
| `foreman.toml` | Codex 程序、训练/评测命令、超时、优化标准 |
| `workspace/model.py` | Codex 可以修改的模型和训练实现 |
| `workspace/config.json` | Codex 可以修改的超参数 |
| `workspace/train.py` | 固定训练入口 |
| `workspace/train.csv` | 实际 CSV 训练集 |
| `workspace/CONTRACT.md` | 模型接口和 checkpoint 约定 |
| `trusted/verify.py` | 在工作区外运行的独立验证程序 |
| `trusted/validation.csv` | 固定验证数据 |

`doctor` 检查配置、平台和 CLI 是否存在，不会调用模型，也不能证明登录有效。
Codex 默认使用账号可用的默认模型；需要指定时，在 `[codex]` 下增加
`model = "你的账号可用的模型名"`，不要照搬一个不确定可用的名称。

## 3. 建议先停在 Codex 调用前

```bash
python -m agentforeman start --project ./my-experiment \
  --goal "在保持模型权重接口不变的前提下，提高验证准确率。只实现一个有明确假设的候选方案。" \
  --until codex
```

这会真实训练并评测基线，然后暂停，不消耗 Codex 调用。记下打印的 `Run ID`。

```bash
python -m agentforeman status RUN_ID --project ./my-experiment
```

确认 `baseline_metric` 有值，接着执行：

```bash
python -m agentforeman resume RUN_ID --project ./my-experiment
```

此时会调用你的 Codex，让它修改文件，再由管理器启动训练和独立评测。

## 4. 中途退出后恢复

在终端按 Ctrl-C 退出管理器。已启动的后台作业会继续。重新执行同一个 `resume`
会检查已有作业和结果，继续原来的阶段。**不要重新 start**，那会创建新实验。

Linux/macOS 断开 SSH 时可把管理器放在 tmux 中；监督进程使用独立 session，
Windows 监督进程使用原生 detached 标志。机器或
容器的生命周期策略仍可能清理整个用户会话。关机重启不会让内存中的训练自动继续；
详情见 `RECOVERY.md`。

```bash
python -m agentforeman list --project ./my-experiment
python -m agentforeman events RUN_ID --project ./my-experiment
python -m agentforeman report RUN_ID --project ./my-experiment
```

需要终止训练时，用 `cancel RUN_ID`，它与暂停管理器不同。

## 5. 如果出错

错误会保存在状态和对应 job 日志里。常见路径：

`.foreman/runs/RUN_ID/jobs/codex-00/stderr.log`

缺少 CLI 时先安装并登录；登录失败或额度不足时处理账号状态。然后：

```bash
python -m agentforeman resume RUN_ID --project ./my-experiment --retry-failed
```

重试会产生新尝试，不会覆盖旧日志。超时值或命令在创建 run 时已冻结；如需调整
`foreman.toml`，应创建一个新 run，旧 run 仍可审计。训练失败重试会从头训练该候选，
当前版本没有通用的训练 checkpoint 续训协议。

## 6. 拿到什么

完成后会输出 `archive.zip` 路径。压缩包里包含基线和候选代码、训练配置、CSV、
权重、独立验证结果、修改补丁、日志、事件记录、文件校验表和复现脚本。

`status=completed` 表示闭环完成；`objective_met=false` 表示候选没有达到配置的
改进要求。系统不会把实验执行成功当作算法改进成功。

解压归档，在归档目录运行：

```bash
python reproduce.py --baseline
python reproduce.py
```

## 7. 下一步接你的项目

先看 `EXTENDING.md`。你需要提供：可以修改的文件列表、训练命令、可信评测命令、
数值指标和工件格式。当前 manager 是明确的状态机，负责一个候选的执行可靠性；
多轮自主选题、模型化 manager、多个 worker 和 GPU 调度列在后续路线中。
