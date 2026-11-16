# Codex Dynamic Bridge 操作手册

[中文](README.md) | [English](README.en.md)

[项目首页](../../README.md) · [最新 Release](https://github.com/GuraQwQ/codex-dynamic-bridge/releases/latest) · [许可证](LICENSE)

本插件连接 Antigravity 已开放的本机 CDP，并按需使用官方 `agy` 和 Companion Sidecar。下面的 CLI 示例在本目录执行；正常使用可直接让已加载技能的 Codex 完成。

## 安装与环境

主要支持 Windows 10/11、Python 3.10+、支持 `codex plugin` 的 Codex，以及已安装的 Antigravity。桌面读取和控制需要 Antigravity 正在运行，并生成 `%APPDATA%\Antigravity\DevToolsActivePort`；元数据命令仅依赖 Python 标准库。

先选择工具和依赖的安装位置。`CODEX_HOME` 默认是用户主目录下的 `.codex`，可在安装前改为非系统盘：

```powershell
$env:CODEX_HOME = 'F:\.codex'
[Environment]::SetEnvironmentVariable('CODEX_HOME', 'F:\.codex', 'User')
python --version
codex plugin --help
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
codex plugin list --json --marketplace codex-dynamic-bridge --available
```

更改环境变量后重新启动 Codex。该设置选择后续数据目录，不会自动迁移已有数据。安装插件后新建 Codex 会话以加载技能。

桌面命令需要 Python Playwright。先检查解释器路径，确认依赖目标位于所选磁盘，再安装到同一个 Python：

```powershell
python -c "import sys; print(sys.executable)"
python -m pip install --no-cache-dir playwright
python -c "import sys, playwright; print(sys.executable); print('Playwright OK')"
```

无需运行 `playwright install`：插件连接的是 Antigravity 已经运行的 Chromium。Python 解释器、虚拟环境和 pip 的依赖目标需单独选择，`CODEX_HOME` 不控制它们。

安装后先发送只读请求：

```text
使用 Codex Dynamic Bridge 列出 Antigravity 当前可发现的会话
```

固定版本可从[发布页](https://github.com/GuraQwQ/codex-dynamic-bridge/releases/latest)下载 `codex-dynamic-bridge-0.6.0.zip`，解压到非系统盘。解压根包含 `.agents/plugins/marketplace.json` 和 `plugins/codex-dynamic-bridge/`，将该根目录注册为本地 marketplace：

```powershell
codex plugin marketplace add F:\tools\codex-dynamic-bridge-0.6.0
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

把示例替换为实际解压根的绝对路径。后续 CLI 命令从其 `plugins/codex-dynamic-bridge` 子目录运行，源码检出使用相同布局。GitHub 与本地包 marketplace 同名，切换前用 `codex plugin marketplace list` 核对已有来源再处理。

## 完整能力装载

只读探测不会安装工具：

```powershell
python -m bridge.cli doctor
python -m bridge.cli setup status
python -m bridge.cli companion status
```

需要官方 CLI、事件、审批通知和计划任务时，在 Codex 中明确授权：

```text
使用 Codex Dynamic Bridge 完成此任务；如果完整能力尚未装载，自动安装官方 agy 并注册全局 Companion 到 default-cli-project，我授权此次装载
```

Agent 从已安装的插件根目录运行：

```powershell
python -m bridge.cli setup install --confirm-setup
```

Windows 上 `agy` 默认安装到 `$CODEX_HOME/tools/agy`；下载按官方 HTTPS 清单、可信域名和 SHA-512 校验后原子写入。默认拒绝未授权的系统盘目标；可用 `--agy-dir F:\tools\agy` 选择其他位置，仅在用户明确同意系统盘写入时使用 `--allow-system-drive`。

Companion 全局注册到 `$HOME/.gemini/config/plugins/codex-dynamic-bridge`，并合并 `$HOME/.gemini/config/config.json` 中自己的 Sidecar 项。这个官方目录独立于 `CODEX_HOME`；完整装载授权包含对应的小型配置写入。默认项目为 `default-cli-project`。

只安装 Companion，或指定其他项目时：

```powershell
python -m bridge.cli companion install-global --confirm-install
python -m bridge.cli companion install-global --project-id <project-id> --confirm-install
```

相同文件、配置及 Hook 返回 `updated: false`。已有安装需要更新且 Antigravity 或该 Sidecar 仍运行时，先完成工作并退出对应实例，再重复安装。返回 `restartRequired: true` 时重启 Antigravity，随后复查 `companion status` 和 `doctor`。安装器不会自动关闭应用。

更新和下载缓存清理仍使用同一入口：

```powershell
python -m bridge.cli setup status --check-update
python -m bridge.cli setup install --refresh-agy --confirm-setup
python -m bridge.cli setup clean --confirm-clean
```

默认保留外部管理的 `agy`；显式指定 `--agy-dir` 才选择其他安装目录。`pending: true` 表示文件占用、已保留验证缓存，重复安装可重试；它不表示升级完成。`setup clean` 只清理本安装器的下载缓存。发布清单与摘要更新流程目前支持 Windows；Unix 分支使用官方安装脚本，已有托管程序的刷新返回 `updateSkipped`。

## 后端与输出

| 后端 | 用途 |
| --- | --- |
| Desktop CDP | 可见消息、可访问性快照、模型/项目界面与桌面工作流 |
| 官方 `agy` | 指定项目、模型、effort、agent 的结构化 headless 任务 |
| Companion Sidecar | 官方 `agentapi` 发送、Hook 事件、完成等待和计划任务 |

`doctor` 报告实际可用能力及 `agy` 版本和选项。普通 `conversation send --backend auto` 在发现目标桌面页时使用桌面发送流程：空闲时验证直接发送，执行中验证立即发送排队项。指定模型、effort、agent 或使用 `resume` 时选择支持这些参数的 `agy`。写操作开始后不会自动切换后端重发；后置验证报错可能发生在实际发送之后。

CLI 默认输出一份紧凑 JSON，保留 ID、业务结果、状态与投递回执；成功工作流省略页面前后快照。显式 `inspect`/`snapshot` 仍返回所请求的详情，错误或结果未知时保留诊断。全局 `--verbose` 放在子命令前：

```powershell
python -m bridge.cli --verbose control inspect --id <id>
```

Codex 使用的模型不决定工具是否可用；宿主提供的电脑控制和异步等待是可选增强。`--model` 选择 Antigravity 执行模型。

## 发现与增量读取

```powershell
python -m bridge.cli discover
python -m bridge.cli discover-pages
python -m bridge.cli control inspect --id <id>
python -m bridge.cli control read --id <id>
python -m bridge.cli control snapshot --id <id>
python -m bridge.cli control read --id <id> --after-message <nextMessageIndex> --limit 5
```

多会话时检查候选的 `hasFocus`，选择唯一聚焦页，或显式指定 ID。没有会话但存在唯一可信外壳页时，可以直接 `conversation open-new` 创建首个任务。

默认正文读取按会话消息区域排除侧栏、历史会话和菜单。增量读取返回 `nextMessageIndex`；`running: true` 时末条游标保留，便于再次读取还在生成的回复。只有需要整页内容时才显式使用 `--selector body`；也可用 `--selector "main"` 缩小区域。

## 新建、发送和恢复

提示词优先通过 UTF-8 标准输入传入。以下文件只是开发示例，Agent 可直接传递用户文本，无需让用户创建文件。PowerShell 示例建议使用 PowerShell 7：

```powershell
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation open-new --prompt-stdin --confirm-conversation
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation open-new --project-id <project-id> --prompt-stdin --confirm-conversation
Get-Content -Raw -Encoding UTF8 .\supplement.txt | python -m bridge.cli conversation send-now --id <id> --prompt-stdin --confirm-send
Get-Content -Raw -Encoding UTF8 .\supplement.txt | python -m bridge.cli conversation send --conversation-id <id> --backend auto --prompt-stdin --confirm-send
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation resume --conversation-id <id> --prompt-stdin --confirm-send
python -m bridge.cli conversation wait --conversation-id <id>
```

结构化 headless 任务可指定项目、模型与 effort；本地目录也可创建为项目：

```powershell
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation new --project-id <project-id> --model <slug> --effort high --prompt-stdin --confirm-create
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation new --project-path F:\work\my-project --new-project --prompt-stdin --confirm-create
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation new --backend agy --stream --project-id <project-id> --prompt-stdin --confirm-create
```

流式 `init` 会立即关联会话和回执，进度实时入账，Bridge 标准输出仍为最终 JSON。`turn_completed` 表示 CLI 回合返回，`stopped` 需要完全空闲的 Hook；两者都不代表验收通过。

桌面会话操作：

```powershell
python -m bridge.cli conversation switch --id <id> --target <title> --confirm-conversation
python -m bridge.cli conversation rename --id <id> --name <name> --confirm-conversation
python -m bridge.cli conversation fork --id <id> --project-id <project-id> --confirm-conversation
python -m bridge.cli conversation cancel --id <id> --confirm-conversation
```

## 模型、项目、设置与产物

```powershell
python -m bridge.cli model list
python -m bridge.cli model desktop-list --id <id>
python -m bridge.cli model set --id <id> --model <visible-name> --confirm-model
python -m bridge.cli project list
python -m bridge.cli project open --project-id <project-id> --confirm-project
python -m bridge.cli project new --confirm-project
python -m bridge.cli settings read --id <id>
python -m bridge.cli settings set --id <id> --label <accessible-label> --value <value> --confirm-settings
python -m bridge.cli usage --id <id>
python -m bridge.cli artifact list --conversation-id <id>
python -m bridge.cli artifact read --conversation-id <id> --path <relative-path>
python -m bridge.cli artifact proceed --id <id> --confirm-artifact
```

## 监工与事件

监工先读取当前会话 Review 页所属变更，再用对应仓库的 `git status`、`git diff` 和测试结果补全。工作区优先取 Hook 的 `workspacePaths`，缺失时用用户给出的路径；多候选时先核对。需要解释变更、处理错误或审批时再读取相关消息。

```powershell
python -m bridge.cli review changes --id <id>
python -m bridge.cli activity --conversation-id <id>
python -m bridge.cli event sync
python -m bridge.cli event list --conversation-id <id>
python -m bridge.cli task inspect --conversation-id <id>
python -m bridge.cli task submissions
python -m bridge.cli task link --conversation-id <id> --codex-task-id <codex-id> --project-id <project-id>
python -m bridge.cli task record-review --submission-id <id> --verdict passed --evidence <absolute-report-path> --confirm-review
```

发送前保存投递回执。`conversation wait` / `event wait` 默认使用最新回执的时间下界，必要时可显式传 `--after <observedAt>`。`event sync` 按端点及会话范围保存分页进度，中断后重试会重读未完成页并去重；`doctor` 的 `eventStreamCursor: true` 表示支持增量游标。

`accepted`、观察到执行停止和验收 `passed` 分别记录。`task record-review` 保存监工结论和证据文件摘要，不执行测试。`activity.agents` 展示已观察到的父子关系、角色和实际工作区。完整状态与恢复说明见[监工生命周期](skills/codex-dynamic-bridge/references/supervision.md)。

## 审批与计划任务

Companion 的 `PreToolUse` 只匹配 `run_command|ask_permission`，返回 Antigravity 的 `ask` 决策。安装或更新 Hook 后重启一次 Antigravity，并在任务投递后主动等待审批事件：

```powershell
python -m bridge.cli event wait-approval --conversation-id <id> --tool-name run_command --timeout-seconds 300
python -m bridge.cli approval inspect --id <id>
python -m bridge.cli approval respond --id <id> --decision allow --option-name <exact-option-text> --button-name <exact-submit-button-name> --tool-name run_command --event-observed-at <observedAt> --dialog-fingerprint <fingerprint> --confirm-approval
```

`observedAt` 来自对应等待事件，指纹来自 `approval inspect` 的 `detail.approval.fingerprint`，选项和按钮使用当前对话框的精确名称。拒绝时将 `--decision` 改为 `deny`，同时选择实际拒绝选项。审批框变化后重新检查；同一事件不能重复响应。用户可授权当前具体命令，也可授权监工在限定任务范围内自主判断和审批。

独立于 Codex 宿主的 Antigravity 周期任务最小间隔为 60 秒：

```powershell
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli schedule create --prompt-stdin --interval-seconds 3600 --confirm-schedule
python -m bridge.cli schedule list
python -m bridge.cli schedule remove --schedule-id <id> --confirm-schedule
```

周期监工优先使用宿主现有计划任务。Sidecar 计划任务会持久化提示词以便再次执行；停止调度不代表正在执行的任务已经取消。

## 低层页面控制与链接

优先使用领域命令。确需低层控制时，先观察实际 DOM 并选择唯一控件；示例选择器需匹配当前页面：

```powershell
python -m bridge.cli control get --id <id> --selector "button[data-testid='send-button']"
python -m bridge.cli control wait --id <id> --selector "body" --state visible
python -m bridge.cli control click --id <id> --selector "button[data-testid='send-button']" --confirm-control
Get-Content -Raw -Encoding UTF8 .\input.txt | python -m bridge.cli control fill --id <id> --selector "[contenteditable='true']" --text-stdin --confirm-control
python -m bridge.cli control press --id <id> --selector "[contenteditable='true']" --key Enter --confirm-control
```

只在确认多匹配项顺序含义后使用 `--nth <zero-based-index>`。完整参数见对应命令的 `--help`。

```powershell
python -m bridge.cli live --id <id>
python -m bridge.cli list
python -m bridge.cli remove --id <link-id>
python -m bridge.cli sync --source .\external-links.json
```

## 授权与数据位置

发现、读取、快照、列表、等待和状态检查为只读。会话、页面、模型、项目、设置、产物、审批和计划任务写入分别使用对应 `--confirm-*` 参数，代表用户已授权该操作。结果未知的写操作应先核对状态和回执，再决定后续动作。

插件仅连接本机回环端点，不提供任意 JavaScript、任意导航、上传下载或凭据读取命令，也不直接改写 Antigravity 私有会话存储。Hook 只保留会话、工具名、审批状态等白名单字段，不保存完整工具参数。

| 数据 | 默认位置 |
| --- | --- |
| 托管 `agy` | `$CODEX_HOME/tools/agy` |
| 安装下载缓存 | `$CODEX_HOME/tools/cache/agy-staging` |
| 链接、任务、事件和回执 | `$CODEX_HOME/plugins/data/codex-dynamic-bridge` |
| Companion 文件与全局配置 | `$HOME/.gemini/config` |
| Companion 事件和计划任务 | `$HOME/.gemini/antigravity/sidecar_data/codex-dynamic-bridge/codex-bridge/data` |

`CODEX_HOME` 未设置时使用 `$HOME/.codex`。Sidecar 计划提示词保存在 `schedules.json`，模型回复正文不写入该计划文件。

## 更新与卸载

更新 Codex 端后新建会话加载技能：

```powershell
codex plugin marketplace upgrade codex-dynamic-bridge
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

随后检查 Companion 与 `agy`，按前述安装流程更新两端。移除 Codex 插件前，先在仍可使用插件时卸载 Companion：

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin marketplace remove codex-dynamic-bridge
```

卸载只删除本 Companion 目录和自己的 Sidecar 配置项，默认保留链接、事件、回执及计划任务数据。`restartRequired: true` 时完全退出并重启 Antigravity；最终应为 `installed: false`、`enabled: false`、`endpointReady: false`。保留的旧 `endpoint.json` 不代表服务可用。彻底清理前先核对上表路径并确认计划任务数据不再需要。

## 故障排查

| 情况 | 处理 |
| --- | --- |
| 没有 `codex plugin` | 检查 `codex plugin --help`，更新到支持插件的 Codex 构建 |
| marketplace 下载失败或名称冲突 | 检查 GitHub 连接与 `codex plugin marketplace list`，核对旧来源后再处理 |
| 找不到调试端口 | 确认 Antigravity 已启动及 `%APPDATA%\Antigravity\DevToolsActivePort` 存在；旧端口可通过重启刷新 |
| 缺少 Playwright | 打印 `sys.executable`，在同一 Python 和选定磁盘安装后验证导入 |
| 无会话但有客户端 | `discover-pages`，从唯一可信外壳 `conversation open-new` |
| 补充排队 | 检查 `send-now` 回执及当前页面；结果未知时先核对，避免重复发送 |
| 没有审批事件 | 检查 Companion、Hook 路径和重启状态，再运行 `event wait-approval` |
| 安装中断或文件占用 | 重复 `setup install`；`pending` 保留验证缓存供重试 |
| DOM 更新后控制失效 | 用 `control inspect`/`snapshot` 重新观察页面和选择器 |

Windows 下载失败不会替换原 `agy.exe`；断点缓存会校验长度、响应范围和 SHA-512。错误报告请附 Codex、Python、Antigravity 版本、命令和诊断，先移除正文、令牌及敏感路径。

## 开发

```powershell
git clone https://github.com/GuraQwQ/codex-dynamic-bridge.git
Set-Location .\codex-dynamic-bridge\plugins\codex-dynamic-bridge
python -B -m bridge.cli --help
python -B -m bridge.cli doctor
```

源码主要位于 `bridge/`，Antigravity 端位于 `companion/antigravity-plugin/`，Agent 使用说明位于 [SKILL.md](skills/codex-dynamic-bridge/SKILL.md)。`doctor` 只读探测本机可用性；真实页面发送、审批和官方 CLI 行为仍需要对应运行环境验证。

MIT，详见 [LICENSE](LICENSE)。
