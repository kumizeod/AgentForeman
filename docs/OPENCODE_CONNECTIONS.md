# OpenCode 1 / 2 接入排查

v0.14.1 修复了 Windows CLI 定位和 OpenCode 2 协议兼容；没有修改现有 Codex 账号、API 配置或认证文件。

## 已能打开 OpenCode，但页面说没有检测到

这表示控制台进程无法定位它能调用的 CLI，不代表没有安装或没有购买 Go。可能是控制台启动时的 PATH 尚未更新，或新版 npm 安装入口与旧版不同。

- 自动检测会补查 Windows 用户/系统 PATH 和常见 CLI 安装目录；OpenCode 2 的 `@opencode/cli` 原生入口不再被当作 Node.js 脚本。
- 如仍找不到，点击添加连接中的「指定已安装的客户端路径」，填写真实 CLI 的完整路径，再添加测试。不要上传程序，也不要填启动参数。
- 在能够运行 OpenCode 的 PowerShell 里查看路径：

```powershell
Get-Command opencode | Select-Object CommandType, Source
opencode --version
```

如果来自桌面程序，要选择它的 CLI，而不是 GUI 程序。不要为了排查直接覆盖已经可用的安装。

## OpenCode 2 与旧版的区别

系统先用 `--version` 识别 1 / 2；无法识别则明确失败，不盲猜协议。

- 2.x 使用 `run --standalone`，避免继承一个已经启动、环境与当前任务不一致的共享服务。
- 子进程的 `PWD` 与受监督的工作目录保持一致；2.2.1 会优先使用 `PWD`，仅设置进程 cwd 不够。
- 在系统自己的任务 context 中写入 V2 `permissions` / `agents` 配置并禁止工具；不覆盖用户配置或登录目录。客户端配置约束不等于操作系统沙箱。
- 显式指定标题，避免额外调用模型生成标题。
- 2.2.1 的 JSON 输出可能只有 text 记录，没有旧版 `step_finish`。必须同时具备原生 supervisor 的成功退出回执、无 error/tool 事件、符合冻结 schema 的单一 JSON 答案，才能接受结果。退出不确定或缺失回执仍然失败。
- 如果手动选择模型，请使用 `opencode models` 中的真实 `provider/model` 标识；界面的显示名称不一定是模型 ID。原有 Go 登录由官方客户端使用，不导入订阅令牌。

错误信息也改为区分 CLI 定位、模型访问、网络与认证；不再凭日志时间戳中偶然出现的 401/403 数字判断认证失败。

## 验证边界

`tests.test_opencode_native` 可用真实 OpenCode 2.2.1 CLI、真实原生 supervisor 和本地模拟模型 HTTP 流验证全流程，确认一次任务只有一次模型请求。设置 `FOREMAN_OPENCODE_BINARY` 指向已经安装的 CLI 后运行：

```powershell
python -m unittest tests.test_opencode_native -v
```

没有设置该变量时此项跳过；CI 另外安装固定版本的 Linux CLI 运行它。Windows 定位与 npm 原生入口另有平台测试。自动化未使用真实 Go 订阅；实际账号权限、模型可用性和服务商计费仍需在部署电脑测试。

官方说明：[V2 安装](https://opencode.ai/v2/docs/)、[CLI](https://opencode.ai/v2/docs/cli/)、[权限迁移](https://opencode.ai/v2/docs/permissions/)。
