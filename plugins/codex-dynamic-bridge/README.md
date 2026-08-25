# Codex Dynamic Bridge

[中文](#中文) | [English](#english)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Codex Plugin](https://img.shields.io/badge/Codex-Plugin-black.svg)](https://github.com/openai/codex)

Codex Dynamic Bridge 是一个面向 Windows 的 Codex 插件，通过 Antigravity 已开放的本机 Chromium DevTools Protocol（CDP）端口发现、读取并在明确授权后操作 Antigravity 会话。

Codex Dynamic Bridge is a Windows-focused Codex plugin that discovers and reads Antigravity sessions, and controls them after explicit authorization, through Antigravity's existing local Chromium DevTools Protocol (CDP) endpoint.

---

## 中文

### Agent 快速安装

先确认本机具备 Python 3.10+，且 Codex 支持 `plugin` 子命令：

```powershell
python --version
codex --version
codex plugin --help
```

然后执行以下两条安装命令：

```powershell
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

安装后新建一个 Codex 任务，然后发送：

```text
使用 Codex Dynamic Bridge 发现并读取 Antigravity 当前会话
```

需要完整任务控制能力时，在新任务中一次性发送：

```text
使用 Codex Dynamic Bridge 完成此任务；如果完整能力尚未装载，自动安装官方 agy 并注册全局 Companion 到 default-cli-project，我授权此次装载
```

Agent 会先只读检查状态，再执行 `setup ensure --confirm-setup`。`agy` 默认安装到 `$CODEX_HOME\tools\agy`，Windows 通过官方 HTTPS manifest、可信域名和 SHA-512 校验；Companion 是一次性全局注册，不需要逐项目安装，也不会热注入 Electron。

若 `CODEX_HOME` 未设置且工具不能落系统盘，先把它设置到非系统盘，例如 `F:\.codex`。这会迁移 `agy`、下载缓存和 Codex 插件缓存。Companion 必须使用 Antigravity 官方 `$HOME\.gemini\config`；完整装载授权明确包含这项配置写入，禁止写用户主目录的环境不能启用 Companion。

需要页面控制能力时，确保 Codex 所使用的 Python 环境已安装 Playwright：

```powershell
python -m pip install playwright
```

无需运行 `playwright install`。插件连接的是 Antigravity 已经运行的 Chromium，不会下载或启动另一个浏览器。

可用以下命令确认 Playwright 安装到了当前 `python`：

```powershell
python -c "import sys, playwright; print(sys.executable); print('Playwright OK')"
```

### 功能

- 发现 Antigravity 当前暴露的本机会话及其 conversation ID。
- 检查页面焦点、加载状态、视口和活动元素。
- 读取当前会话或指定元素的完整可见文本。
- 生成可访问性快照，并按角色与可访问名称控制 UI。
- 新建、继续、等待、切换、重命名、分叉或取消会话。
- 列出和切换模型，指定 reasoning effort，并读取模型用量。
- 打开或创建项目，读取和修改全局/项目设置。
- 读取产物、批准计划、汇总工具与子 Agent 活动。
- 通过 Companion Sidecar 接收 Hook 事件并运行计划任务。
- 运行中将补充信息通过 `Send Now` 立即注入，不等待当前回合结束。
- 在 `run_command`/`ask_permission` 前接收审批请求并检查当前审批对话框。
- 维护 Codex 任务与 Antigravity 会话的双向映射。
- 等待指定元素进入 attached、detached、visible 或 hidden 状态。
- 在用户明确授权具体动作后点击、填充或发送按键。
- 保存、列出、合并和删除本地会话链接元数据。
- 只连接回环地址，不启动、关闭或登录 Antigravity。

### 环境要求

- Windows 10/11。
- 已安装并正在运行的 Antigravity。
- Antigravity 已创建 `%APPDATA%\Antigravity\DevToolsActivePort`。
- 已安装支持插件命令的 Codex Desktop 或 Codex CLI。
- Python 3.10 或更高版本。
- 元数据命令只使用 Python 标准库。
- `control` 命令需要 Python Playwright，但不需要单独安装 Chromium。
- 结构化 headless 会话可选使用官方 `agy` CLI。
- 事件、等待和计划任务可选使用仓库内的 Antigravity Companion Sidecar。

### 能力层级

首先运行：

```powershell
python -m bridge.cli doctor
```

插件会探测三个后端：

1. **Desktop CDP**：读取、可访问性快照及语义桌面控制。
2. **Antigravity CLI (`agy`)**：按项目、模型和 effort 新建或继续 headless 会话，并返回结构化 JSON。
3. **Companion Sidecar**：通过官方 `agentapi` 新建/发送，接收 Hook 完成事件并管理计划任务。

需要监工或中途追加时，`conversation send --backend auto` 会在发现桌面会话后强制走 Desktop CDP 的 `Send Now`，不会把补充留在 Sidecar 队列；也可以直接使用 `conversation send-now`。只有显式指定 Sidecar，或目标没有桌面页面时才走 Sidecar/`agy`。

未安装可选后端时，`doctor` 会报告能力缺口。只有用户明确要求使用本插件完成任务并授权完整装载时，Agent 才可执行 `setup ensure --confirm-setup`；普通发现或只读请求不能触发安装。写操作开始后不会跨后端自动重试。

### 安装与验证

1. 添加公开 marketplace：

```powershell
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
```

2. 安装插件：

```powershell
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

3. 检查插件是否可见：

```powershell
codex plugin list --json --marketplace codex-dynamic-bridge --available
```

4. 新建 Codex 任务以加载新技能。已有任务不会自动重新加载刚安装的技能。

   - Codex Desktop：点击新建任务。
   - Codex CLI：结束当前交互会话，然后重新运行 `codex`。

5. 在新任务中发送以下只读烟雾测试：

```text
使用 Codex Dynamic Bridge 列出 Antigravity 当前可发现的会话；不要修改页面
```

预期结果是会话 JSON 列表；若 Antigravity 未运行，应收到明确的调试端口文件错误，而不是执行页面修改。

6. 需要稳定事件、等待或计划任务时，在同一个新任务中发送以下授权提示。Codex 会从已安装插件位置运行命令，不需要你查找其缓存目录：

```text
使用 Codex Dynamic Bridge 检查全局 Companion；如果尚未安装，使用默认项目 default-cli-project 一次性安装，我授权此次安装
```

### 推荐提示词

```text
列出 Antigravity 当前可发现的会话
```

```text
读取并总结 Antigravity 当前聚焦会话的工作记录
```

```text
检查 Antigravity 当前页面；不要执行任何页面修改
```

```text
在 Antigravity 会话 <conversation-id> 中点击发送按钮，我授权这次点击
```

只读请求不需要提供 CSS 选择器。多个会话同时存在时，插件会检查焦点；若仍无法唯一确定目标，会要求选择 conversation ID。

普通用户无需创建命令示例中的 `prompt.txt` / `supplement.txt`；Agent 直接把用户文本作为 UTF-8 标准输入传给 `--prompt-stdin`。可直接说“无会话时从唯一可信客户端页面新建并发送此任务”或“把此补充立即发送到当前执行，不要排队”，并明确授权本次发送。停止后的旧会话使用 `conversation resume`，不要把旧 ID 交给 Sidecar 重试。

### 监工模式

Codex 监工默认先运行 `review changes --id <id>` 读取当前 Antigravity 会话 Review 页归属的文件 diff，再用仓库 `git status` 和 git diff 补全验证，避免混入用户原有或其他会话改动。若 Review diff 与当前实现一致，不要求重复修改。只有 diff 无法解释意图、出现错误/审批或用户明确要求时，才读取相关会话片段；授权反馈后通过 `conversation send-now` 立即追加。

git 工作区优先使用 `event sync` / `event list` 的 `workspacePaths`，Hook 未启用时使用用户明确给出的路径；多个候选时只读核对，不猜测。

### 命令参考

以下开发命令应在 `plugins/codex-dynamic-bridge` 目录中运行。正常使用时应直接让 Codex 调用插件技能。

P1-P3 领域命令：

```powershell
# 能力探测
python -m bridge.cli doctor
python -m bridge.cli setup status
python -m bridge.cli setup ensure --confirm-setup
python -m bridge.cli discover-pages

# 无会话时从唯一可信外壳创建首个任务；运行中立即追加补充
python -m bridge.cli project list --id <id>
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation open-new --project-id <project-id> --prompt-stdin --confirm-conversation
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation open-new --prompt-stdin --confirm-conversation
Get-Content -Raw .\supplement.txt | python -m bridge.cli conversation send-now --id <id> --prompt-stdin --confirm-send

# 新建、继续、恢复和等待会话；提示词优先通过标准输入传入
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation new --prompt-stdin --project-id <project-id> --model <slug> --effort high --confirm-create
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation new --prompt-stdin --project-path F:\work\my-project --new-project --confirm-create
Get-Content -Raw .\supplement.txt | python -m bridge.cli conversation send --conversation-id <id> --backend auto --prompt-stdin --confirm-send
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation resume --conversation-id <id> --prompt-stdin --confirm-send
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation send --conversation-id <id> --prompt-stdin --confirm-send
python -m bridge.cli conversation wait --conversation-id <id>

# 桌面会话、模型和项目
python -m bridge.cli conversation switch --id <id> --target <title> --confirm-conversation
python -m bridge.cli conversation rename --id <id> --name <name> --confirm-conversation
python -m bridge.cli conversation fork --id <id> --project-id <project-id> --confirm-conversation
python -m bridge.cli conversation cancel --id <id> --confirm-conversation
python -m bridge.cli model list
python -m bridge.cli model desktop-list --id <id>
python -m bridge.cli model set --id <id> --model <visible-name> --confirm-model
python -m bridge.cli project open --id <id> --project-id <project-id> --confirm-project
python -m bridge.cli project new --id <id> --confirm-project

# 设置、用量、产物、活动和任务映射
python -m bridge.cli settings read --id <id>
python -m bridge.cli settings set --id <id> --label <accessible-label> --value <value> --confirm-settings
python -m bridge.cli usage --id <id>
python -m bridge.cli artifact list --conversation-id <id>
python -m bridge.cli artifact read --conversation-id <id> --path <relative-path>
python -m bridge.cli artifact proceed --id <id> --confirm-artifact
python -m bridge.cli activity --conversation-id <id>
python -m bridge.cli review changes --id <id>
python -m bridge.cli task link --conversation-id <id> --codex-task-id <codex-id> --project-id <project-id>

# Hook 事件和 Sidecar 计划任务
python -m bridge.cli event sync
python -m bridge.cli event list --conversation-id <id>
python -m bridge.cli event wait-approval --conversation-id <id> --tool-name run_command --timeout-seconds 300
python -m bridge.cli approval inspect --id <id>
python -m bridge.cli approval respond --id <id> --decision allow --option-name <exact-option-text> --button-name <exact-submit-button-name> --tool-name run_command --event-observed-at <observedAt> --confirm-approval
python -m bridge.cli approval respond --id <id> --decision deny --option-name <exact-deny-option-text> --button-name <exact-submit-button-name> --tool-name run_command --event-observed-at <observedAt> --confirm-approval
Get-Content -Raw .\prompt.txt | python -m bridge.cli schedule create --prompt-stdin --interval-seconds 3600 --confirm-schedule
python -m bridge.cli schedule list
python -m bridge.cli schedule remove --schedule-id <id> --confirm-schedule
```

### 一次性启用全局 Companion Sidecar

无需再给每个 Antigravity 工作区安装伴生插件。正常使用时，在已加载插件的新 Codex 任务中发送：

```text
使用 Codex Dynamic Bridge 检查全局 Companion；如果尚未安装，使用默认项目 default-cli-project 一次性安装，我授权此次安装
```

Codex 会定位已安装插件并自动检测正在运行的 Antigravity。安装器把 Companion 暂存并原子替换到官方全局目录 `$HOME\.gemini\config\plugins\codex-dynamic-bridge`，同时只合并自己的 Sidecar 配置，不覆盖 `config.json` 的其他字段。

下面的 CLI 只供源码检出目录中的开发和排障使用；先进入仓库内的插件目录：

```powershell
Set-Location .\plugins\codex-dynamic-bridge
python -m bridge.cli companion status
python -m bridge.cli companion install-global --confirm-install
```

Sidecar 使用 Antigravity 官方 `agentapi`，只绑定 `127.0.0.1`，每次启动生成随机令牌。默认项目是官方定义的 `default-cli-project`；需要绑定其他已知项目时才追加 `--project-id <project-id>`。一次配置后，所有 Antigravity 工作区共用该全局 Companion，不必重复复制插件。

安装器是幂等的：文件和配置相同时返回 `updated: false`，无需重启。已有 Companion 需要更新但 Antigravity 正在运行时，安装器会保持零修改并要求完全退出后重试；全新安装到运行中的 Antigravity 时可能返回 `restartRequired: true`。不要让 Codex 代替你关闭应用。重新启动后，可让 Codex “检查 Codex Dynamic Bridge 的 Companion 和 doctor 状态”，或在源码插件目录运行：

```powershell
python -m bridge.cli companion status
python -m bridge.cli doctor
```

成功判据：`companion status` 的 `installed`、`enabled`、`endpointReady` 与 `antigravityRunning` 均为 `true`，`projectId` 为预期项目；`doctor` 的 `sidecar.available` 为 `true`。至少完成一次 Antigravity 任务后，在 Codex 中发送以下只读提示；能返回最新事件才表示 Hook 链路也已工作：

```text
使用 Codex Dynamic Bridge 同步 Sidecar Hook 事件并列出最新 10 条；不要修改 Antigravity 会话
```

这里使用官方全局插件发现机制，不对 Electron 进程做版本敏感的热注入，也不会自动关闭或重启 Antigravity。普通生命周期事件上报失败时 fail-open。`PreToolUse` 只匹配 `run_command|ask_permission`，无论上报是否成功都返回官方 `ask`，绝不使用 `allow` 绕过权限；只保存会话 ID、工具名和审批状态等白名单字段，不保存完整命令参数。需要移除时，先在 Codex 中发送“使用 Codex Dynamic Bridge 卸载全局 Companion，我授权此次卸载”，或在源码插件目录运行：

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
```

卸载只删除本插件目录和 `codex-dynamic-bridge/codex-bridge` 配置项，保留其他 Antigravity 配置与 Sidecar。

Sidecar 运行数据位于 `$HOME\.gemini\antigravity\sidecar_data\codex-dynamic-bridge\codex-bridge\`。定时任务提示词保存在 `data\schedules.json` 才能重复执行，但模型回复正文不会写入该文件。`uninstall-global` 默认保留该运行数据；需要彻底清理时，先确认不再需要计划任务，再核对并手动删除上述 `codex-bridge` 目录。

```powershell
# 发现会话
python -m bridge.cli discover

# 检查页面状态
python -m bridge.cli control inspect --id <conversation-id>

# 读取会话正文；默认只返回 User/Agent 消息，排除侧栏和历史会话
python -m bridge.cli control read --id <conversation-id>

# 缩小读取范围
python -m bridge.cli control read --id <conversation-id> --selector "main"

# 获取唯一元素的摘要状态
python -m bridge.cli control get --id <conversation-id> --selector "button[aria-label='发送']"

# 等待元素
python -m bridge.cli control wait --id <conversation-id> --selector "body" --state visible

# 页面修改必须显式确认
python -m bridge.cli control click --id <conversation-id> --selector "button[aria-label='发送']" --confirm-control
python -m bridge.cli control fill --id <conversation-id> --selector "[contenteditable='true']" --text "内容" --confirm-control
python -m bridge.cli control press --id <conversation-id> --selector "[contenteditable='true']" --key Enter --confirm-control
```

`aria-label` 等选择器取决于 Antigravity 的实际界面语言。示例中的“发送”只适用于对应中文界面，执行前应先观察真实 DOM。

多行或敏感填充内容可通过标准输入传入：

```powershell
Get-Content -Raw .\input.txt | python -m bridge.cli control fill --id <conversation-id> --selector "[contenteditable='true']" --text-stdin --confirm-control
```

链接元数据命令：

```powershell
python -m bridge.cli live --id <conversation-id>
python -m bridge.cli list
python -m bridge.cli remove --id <link-id>
python -m bridge.cli sync --source .\external-links.json
```

### 权限与安全边界

- `doctor`、`discover`、`inspect`、`read`、`snapshot`、`get`、`wait`、设置读取、事件/活动/产物列表是只读操作。
- 用户明确要求读取、总结或检查会话正文时，插件可读取所选会话的完整可见文本，无需额外选择器。
- `click`、`fill` 和 `press` 只有在用户明确授权当前具体动作后才能使用 `--confirm-control`。
- 会话、模型、项目、设置、产物和计划任务分别使用 `--confirm-create/send/conversation/model/project/settings/artifact/schedule`，授权不可互换。
- 审批响应必须绑定唯一事件、当前对话框的精确选项和提交按钮以及 `--confirm-approval`；没有用户对当前可见命令的明确授权时只能通知，不能批准。
- 插件不提供任意 JavaScript、任意导航、关闭页面、文件上传或下载命令。
- 插件只连接 `127.0.0.1`、`localhost` 或 `::1` 上的 Antigravity 会话。
- 页面修改命令返回错误并不能证明页面完全没有发生变化，因此插件不会自动重试写操作。
- 插件不会主动读取无关会话、账号凭据或模型内部状态。
- 插件不直接编辑 Antigravity 的 `app_storage.json`、凭据或私有会话存储。

### 更新

```powershell
codex plugin marketplace upgrade codex-dynamic-bridge
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

移除后重新安装可确保使用刷新后的 marketplace 快照。更新后新建 Codex 任务。

### 卸载

如果启用了 Companion，必须先在仍可使用插件的新 Codex 任务中发送：

```text
使用 Codex Dynamic Bridge 卸载全局 Companion，我授权此次卸载
```

确认 Companion 已卸载后，再移除 Codex 插件和 marketplace：

```powershell
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin marketplace remove codex-dynamic-bridge
```

卸载结果若为 `restartRequired: true`，说明旧 Sidecar 可能仍在当前 Antigravity 进程内运行；完全退出所有 Antigravity 进程并重新启动。最终状态应为 `installed: false`、`enabled: false`、`endpointReady: false`，且 `doctor` 的 `sidecar.available` 为 `false`。`endpointFileExists` 可能因保留运行数据仍为 `true`，不代表 Sidecar 可用。

插件的会话链接元数据默认保留在 `$CODEX_HOME/plugins/data/codex-dynamic-bridge/links.json`；未设置 `CODEX_HOME` 时位于 `$HOME/.codex/plugins/data/codex-dynamic-bridge/links.json`。Companion 运行数据默认保留在 `$HOME/.gemini/antigravity/sidecar_data/codex-dynamic-bridge/codex-bridge/`。需要完全清理时，确认路径及计划任务均无误后再手动删除这些数据。

### 故障排查

**`codex plugin` 子命令不存在**

当前 Codex 构建不支持插件。更新 Codex Desktop 或 Codex CLI，并用 `codex plugin --help` 确认功能可用后再安装。

**marketplace 下载失败或名称冲突**

确认可以访问 GitHub，然后运行 `codex plugin marketplace list` 检查是否已存在名为 `codex-dynamic-bridge` 的其他来源。只有确认旧来源不再需要时，才先移除它再重新添加本仓库。

**未找到 Antigravity 调试端口文件**

确认 Antigravity 正在运行，并检查 `%APPDATA%\Antigravity\DevToolsActivePort` 是否存在。插件不会自行启动 Antigravity。

若文件存在但仍连接失败，重启 Antigravity，使其刷新端口文件；同时确认端口只在本机回环地址监听。

**控制模式提示缺少 Playwright**

先让 Codex 或同一终端打印当前解释器：

```powershell
python -c "import sys; print(sys.executable)"
```

保持在同一终端，并使用刚才打印路径所对应的同一个 `python` 安装和验证 Playwright；不要切换到其他 Python 启动器：

```powershell
python -m pip install playwright
python -c "import playwright; print('Playwright OK')"
```

**发现多个会话**

先运行 `discover`，再对候选会话运行 `control inspect`。选择唯一 `hasFocus: true` 的会话，或显式传入 conversation ID。

**客户端运行但没有会话 / 补充消息一直排队 / 收不到审批事件**

无会话时运行 `discover-pages` 并从唯一可信外壳执行带提示词的 `conversation open-new`。会话运行中使用 `conversation send --backend auto` 或 `conversation send-now`，它们按正文关联 `Send Now` 并验证队列项消失；停止后的会话使用 `conversation resume`。审批链路需先全局安装 Companion 并重启一次 Antigravity；投递任务后主动运行 `event wait-approval`。若 hook 路径曾指向 `.codex-dynamic-bridge.*.tmp`，重新运行一次全局 Companion 安装修复稳定路径，再重启 Antigravity。

**选择器不唯一**

使用更明确的选择器。只有在已经确认多个匹配项的顺序含义后，才使用 `--nth <从 0 开始的序号>`。

**Antigravity 更新后命令失效**

Antigravity 的 DOM 可能变化。先用 `control inspect` 和只读命令重新观察页面，再更新选择器。

### 开发

```powershell
git clone https://github.com/GuraQwQ/codex-dynamic-bridge.git
Set-Location .\codex-dynamic-bridge\plugins\codex-dynamic-bridge
python -m bridge.self_test
```

项目结构：

```text
.
|-- .agents/plugins/marketplace.json
|-- plugins/codex-dynamic-bridge/
|   |-- .codex-plugin/plugin.json
|   |-- bridge/
|   |   |-- cli.py
|   |   |-- control.py
|   |   |-- runtime.py
|   |   |-- state.py
|   |   `-- self_test.py
|   |-- companion/
|   |   |-- install.py
|   |   `-- antigravity-plugin/
|   |-- skills/codex-dynamic-bridge/SKILL.md
|   `-- README.md
|-- LICENSE
`-- README.md
```

运行测试：

```powershell
Set-Location .\plugins\codex-dynamic-bridge
python -m compileall -q bridge
python -m bridge.self_test
Set-Location .\companion\antigravity-plugin\sidecars\codex-bridge
python .\self_test.py
```

提交 Issue 时请附上 Codex 版本、Python 版本、Antigravity 版本、执行的命令和完整错误信息，并先移除会话正文、令牌和本机路径等敏感内容。

### 许可证

MIT，详见 [LICENSE](LICENSE)。

---

## English

### Agent quick install

First verify that Python 3.10+ is available and that Codex supports the `plugin` command:

```powershell
python --version
codex --version
codex plugin --help
```

Then run these two installation commands:

```powershell
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

Start a new Codex task after installation, then ask:

```text
Use Codex Dynamic Bridge to discover and read the current Antigravity session.
```

For full task control, authorize one-time setup in the new task:

```text
Use Codex Dynamic Bridge to complete this task. If full capabilities are not loaded, automatically install the official agy and register the global Companion for default-cli-project; I authorize this setup.
```

The agent checks status first, then runs `setup ensure --confirm-setup`. `agy` defaults to `$CODEX_HOME\tools\agy` and is verified against the official HTTPS manifest, trusted host, and SHA-512 on Windows. Companion registration is global and one-time, not per project, and does not inject into Electron.

If tools must stay off the system drive, set `CODEX_HOME` to a non-system-drive path such as `F:\.codex` first. This relocates `agy`, download data, and Codex plugin caches. Companion must use Antigravity's official `$HOME\.gemini\config`; full-setup authorization includes that configuration write, and an environment that forbids user-profile writes cannot enable Companion.

For page-control features, ensure Playwright is available in the Python environment used by Codex:

```powershell
python -m pip install playwright
```

Do not run `playwright install`. The plugin connects to Antigravity's existing Chromium instance; it does not download or launch another browser.

Verify that Playwright is installed in the active `python` environment:

```powershell
python -c "import sys, playwright; print(sys.executable); print('Playwright OK')"
```

### Features

- Discover locally exposed Antigravity sessions and their conversation IDs.
- Inspect focus, load state, viewport, and the active element.
- Read the complete visible text of the current session or a selected element.
- Capture accessibility snapshots and target controls by role and accessible name.
- Create, continue, wait for, switch, rename, fork, or cancel conversations.
- List and switch models, set reasoning effort, and inspect model usage.
- Open or create projects and read or update global/project settings.
- Read artifacts, approve plans, and summarize tool and subagent activity.
- Receive Hook events and run schedules through the Companion Sidecar.
- Inject supplements into a running task through `Send Now` instead of waiting for the turn to finish.
- Receive pre-execution approval requests for `run_command`/`ask_permission` and inspect the active approval dialog.
- Maintain bidirectional Codex-task to Antigravity-conversation mappings.
- Wait for an element to become attached, detached, visible, or hidden.
- Click, fill, or press keys only after the user explicitly authorizes the specific action.
- Save, list, merge, and remove local session-link metadata.
- Connect only to loopback addresses without starting, closing, or signing in to Antigravity.

### Requirements

- Windows 10/11.
- Antigravity installed and running.
- Antigravity has created `%APPDATA%\Antigravity\DevToolsActivePort`.
- Codex Desktop or Codex CLI with plugin commands.
- Python 3.10 or newer.
- Metadata commands use only the Python standard library.
- `control` commands require Python Playwright, but no separate Chromium installation.
- Structured headless conversations may optionally use the official `agy` CLI.
- Events, completion waits, and schedules may optionally use the included Antigravity Companion Sidecar.

### Capability tiers

Start with:

```powershell
python -m bridge.cli doctor
```

The plugin detects three backends:

1. **Desktop CDP**: reads, accessibility snapshots, and semantic desktop control.
2. **Antigravity CLI (`agy`)**: creates or continues headless conversations by project, model, and effort with structured JSON output.
3. **Companion Sidecar**: uses official `agentapi` calls, receives Hook completion events, and manages schedules.

For supervision, immediate supplements, or first-response approval handling on a running desktop page, prefer Desktop CDP `open-new`/`send-now`; they return as soon as the UI accepts input so Codex can immediately wait for Hooks. Use Companion/`agy` for headless work without live intervention.

When an optional backend is absent, `doctor` reports it. The agent may run `setup ensure --confirm-setup` only when the user explicitly asks the plugin to complete a task and authorizes full setup. Ordinary discovery and read-only requests never authorize installation. A failed write is never retried automatically through another backend.

### Install and verify

1. Add the public marketplace:

```powershell
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
```

2. Install the plugin:

```powershell
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

3. Verify that Codex can see it:

```powershell
codex plugin list --json --marketplace codex-dynamic-bridge --available
```

4. Start a new Codex task so the newly installed skill is loaded. Existing tasks do not automatically reload newly installed skills.

   - Codex Desktop: create a new task.
   - Codex CLI: end the current interactive session, then run `codex` again.

5. Run this read-only smoke test in the new task:

```text
Use Codex Dynamic Bridge to list currently discoverable Antigravity sessions; do not modify the page.
```

The expected result is a JSON session list. If Antigravity is not running, expect a clear DevTools port-file error rather than a page mutation.

6. If you need stable events, completion waits, or schedules, send this authorization prompt in the same new task. Codex runs it from the installed plugin location, so you do not need to find its cache directory:

```text
Use Codex Dynamic Bridge to check the global Companion. If it is not installed, install it once with the default-cli-project project; I authorize this installation.
```

### Suggested prompts

```text
List the Antigravity sessions that are currently discoverable.
```

```text
Read and summarize the work log in the currently focused Antigravity session.
```

```text
Inspect the current Antigravity page without modifying it.
```

```text
Click the Send button in Antigravity conversation <conversation-id>; I authorize this click.
```

Read-only requests do not require a CSS selector. When multiple sessions exist, the plugin checks focus; if it still cannot select exactly one session, it asks for a conversation ID.

Normal users do not create the example `prompt.txt` / `supplement.txt` files. The agent sends user text directly as UTF-8 standard input to `--prompt-stdin`. Ask it to create from the only trusted shell when no conversation exists, or to send a supplement immediately without queueing, and explicitly authorize that send.

### Supervisor mode

Codex supervision starts with `review changes --id <id>` for the current Antigravity conversation's Review diff, then uses repository `git status` and git diff to complete and verify the picture without conflating pre-existing or other-session changes. If the Review diff already matches the implementation, no duplicate change is requested. Chat fragments are read only when needed; authorized feedback is injected with `conversation send-now`.

The git workspace is taken from Hook `workspacePaths` via `event sync` / `event list`, or from a path explicitly provided by the user before Hooks are active. Multiple candidates are inspected read-only rather than guessed.

### Command reference

Run the following development commands from `plugins/codex-dynamic-bridge`. For normal use, ask Codex to invoke the plugin skill instead.

P1-P3 domain commands:

```powershell
# Capability discovery
python -m bridge.cli doctor
python -m bridge.cli setup status
python -m bridge.cli setup ensure --confirm-setup
python -m bridge.cli discover-pages

# Create the first task from a trusted shell; inject a supplement while a task is running
python -m bridge.cli project list --id <id>
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation open-new --project-id <project-id> --prompt-stdin --confirm-conversation
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation open-new --prompt-stdin --confirm-conversation
Get-Content -Raw .\supplement.txt | python -m bridge.cli conversation send-now --id <id> --prompt-stdin --confirm-send

# Create, continue, and wait; prefer standard input for prompts
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation new --prompt-stdin --project-id <project-id> --model <slug> --effort high --confirm-create
Get-Content -Raw .\prompt.txt | python -m bridge.cli conversation send --conversation-id <id> --prompt-stdin --confirm-send
python -m bridge.cli conversation wait --conversation-id <id>

# Desktop conversations, models, and projects
python -m bridge.cli conversation switch --id <id> --target <title> --confirm-conversation
python -m bridge.cli conversation rename --id <id> --name <name> --confirm-conversation
python -m bridge.cli conversation fork --id <id> --project-id <project-id> --confirm-conversation
python -m bridge.cli conversation cancel --id <id> --confirm-conversation
python -m bridge.cli model list
python -m bridge.cli model desktop-list --id <id>
python -m bridge.cli model set --id <id> --model <visible-name> --confirm-model
python -m bridge.cli project open --id <id> --project-id <project-id> --confirm-project
python -m bridge.cli project new --id <id> --confirm-project

# Settings, usage, artifacts, activity, and task mappings
python -m bridge.cli settings read --id <id>
python -m bridge.cli settings set --id <id> --label <accessible-label> --value <value> --confirm-settings
python -m bridge.cli usage --id <id>
python -m bridge.cli artifact list --conversation-id <id>
python -m bridge.cli artifact read --conversation-id <id> --path <relative-path>
python -m bridge.cli artifact proceed --id <id> --confirm-artifact
python -m bridge.cli activity --conversation-id <id>
python -m bridge.cli review changes --id <id>
python -m bridge.cli task link --conversation-id <id> --codex-task-id <codex-id> --project-id <project-id>

# Hook events and Sidecar schedules
python -m bridge.cli event sync
python -m bridge.cli event list --conversation-id <id>
python -m bridge.cli event wait-approval --conversation-id <id> --tool-name run_command --timeout-seconds 300
python -m bridge.cli approval inspect --id <id>
python -m bridge.cli approval respond --id <id> --decision allow --option-name <exact-option-text> --button-name <exact-submit-button-name> --tool-name run_command --event-observed-at <observedAt> --confirm-approval
python -m bridge.cli approval respond --id <id> --decision deny --option-name <exact-deny-option-text> --button-name <exact-submit-button-name> --tool-name run_command --event-observed-at <observedAt> --confirm-approval
Get-Content -Raw .\prompt.txt | python -m bridge.cli schedule create --prompt-stdin --interval-seconds 3600 --confirm-schedule
python -m bridge.cli schedule list
python -m bridge.cli schedule remove --schedule-id <id> --confirm-schedule
```

### Enable the global Companion Sidecar once

You no longer need to install the companion in every Antigravity workspace. For normal use, send this in a new Codex task that has loaded the plugin:

```text
Use Codex Dynamic Bridge to check the global Companion. If it is not installed, install it once with the default-cli-project project; I authorize this installation.
```

Codex locates the installed plugin and detects a running Antigravity instance. The installer stages and atomically replaces the Companion under the official global directory `$HOME\.gemini\config\plugins\codex-dynamic-bridge`, and merges only its own Sidecar entry without overwriting other `config.json` fields.

The following CLI is only for development and troubleshooting from a source checkout. Enter the repository's plugin directory first:

```powershell
Set-Location .\plugins\codex-dynamic-bridge
python -m bridge.cli companion status
python -m bridge.cli companion install-global --confirm-install
```

The Sidecar uses Antigravity's official `agentapi`, binds only to `127.0.0.1`, and generates a random token at every start. It uses Antigravity's documented `default-cli-project` by default; append `--project-id <project-id>` only for another known project. Once configured, every Antigravity workspace shares this global Companion; no repeated plugin copy is needed.

The installer is idempotent: identical files and configuration return `updated: false` without requiring a restart. If an installed Companion needs an update while Antigravity is running, installation makes no changes and asks you to fully exit before retrying; a fresh install into a running Antigravity may return `restartRequired: true`. Codex must not close the app for you. After relaunching, ask Codex to "check the Codex Dynamic Bridge Companion and doctor status," or run these commands from the source plugin directory:

```powershell
python -m bridge.cli companion status
python -m bridge.cli doctor
```

Success criteria: `companion status` reports `installed`, `enabled`, `endpointReady`, and `antigravityRunning` as `true`, with the expected `projectId`; `doctor` reports `sidecar.available: true`. After at least one Antigravity task completes, send this read-only prompt in Codex. The Hook path is verified only when recent events are returned:

```text
Use Codex Dynamic Bridge to sync Sidecar Hook events and list the latest 10; do not modify the Antigravity conversation.
```

This uses Antigravity's official global plugin discovery instead of version-sensitive hot injection into Electron, and it never closes or restarts Antigravity automatically. Hook commands are written after the staged directory is moved to its final stable path, so reinstalling no longer leaves dead `.tmp` hook paths. Ordinary lifecycle event delivery fails open. `PreToolUse` matches only `run_command|ask_permission` and always returns Antigravity's `ask` decision, even when reporting fails; it never returns `allow` to bypass permissions. Hooks persist only allowlisted fields such as conversation ID, tool name, and approval state, never complete command arguments. To remove it, first tell Codex, "Use Codex Dynamic Bridge to uninstall the global Companion; I authorize this uninstall," or run this from the source plugin directory:

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
```

Uninstall removes only this plugin directory and the `codex-dynamic-bridge/codex-bridge` entry, preserving every other Antigravity setting and Sidecar.

Sidecar runtime data lives under `$HOME\.gemini\antigravity\sidecar_data\codex-dynamic-bridge\codex-bridge\`. Schedule prompts remain in `data\schedules.json` so they can run repeatedly, but model response bodies are never written there. `uninstall-global` retains this runtime data by default. For complete cleanup, first confirm that no schedule is needed, verify the path, and manually remove that `codex-bridge` directory.

```powershell
# Discover sessions
python -m bridge.cli discover

# Inspect page state
python -m bridge.cli control inspect --id <conversation-id>

# Read complete visible text; defaults to body
python -m bridge.cli control read --id <conversation-id>

# Narrow the read scope
python -m bridge.cli control read --id <conversation-id> --selector "main"

# Get a summary of one unique element
python -m bridge.cli control get --id <conversation-id> --selector "button[aria-label='Send']"

# Wait for an element
python -m bridge.cli control wait --id <conversation-id> --selector "body" --state visible

# Mutations require explicit confirmation
python -m bridge.cli control click --id <conversation-id> --selector "button[aria-label='Send']" --confirm-control
python -m bridge.cli control fill --id <conversation-id> --selector "[contenteditable='true']" --text "Content" --confirm-control
python -m bridge.cli control press --id <conversation-id> --selector "[contenteditable='true']" --key Enter --confirm-control
```

Selectors such as `aria-label` depend on Antigravity's actual UI language. The `Send` example only applies to a matching English UI; inspect the real DOM before acting.

Pass multiline or sensitive fill text through standard input:

```powershell
Get-Content -Raw .\input.txt | python -m bridge.cli control fill --id <conversation-id> --selector "[contenteditable='true']" --text-stdin --confirm-control
```

Link-metadata commands:

```powershell
python -m bridge.cli live --id <conversation-id>
python -m bridge.cli list
python -m bridge.cli remove --id <link-id>
python -m bridge.cli sync --source .\external-links.json
```

### Permissions and safety boundaries

- `doctor`, `discover`, `inspect`, `read`, `snapshot`, `get`, `wait`, settings reads, events, activity, and artifact listings are read-only operations.
- When the user explicitly asks to read, summarize, or inspect session content, the plugin may read the complete visible text of the selected session without requiring another selector.
- `click`, `fill`, and `press` may use `--confirm-control` only after the user authorizes that specific action.
- Conversations, models, projects, settings, artifacts, and schedules use separate `--confirm-create/send/conversation/model/project/settings/artifact/schedule` flags; authorization is not interchangeable.
- Approval responses bind a unique event, exact visible option and submit button, and `--confirm-approval`. Without explicit authorization for the currently visible command, the agent may notify but must not approve.
- The plugin does not expose arbitrary JavaScript, arbitrary navigation, page closing, file upload, or download commands.
- It only connects to Antigravity sessions on `127.0.0.1`, `localhost`, or `::1`.
- An error from a mutating command does not prove that the page remained unchanged, so the plugin never retries write actions automatically.
- The plugin does not proactively read unrelated sessions, account credentials, or model-internal state.
- The plugin never edits Antigravity's internal `app_storage.json`, credentials, or private session storage directly.

### Update

```powershell
codex plugin marketplace upgrade codex-dynamic-bridge
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

Removing and reinstalling ensures that Codex uses the refreshed marketplace snapshot. Start a new Codex task after updating.

### Uninstall

If Companion was enabled, first send this in a new Codex task while the plugin is still available:

```text
Use Codex Dynamic Bridge to uninstall the global Companion; I authorize this uninstall.
```

After confirming that Companion is removed, uninstall the Codex plugin and marketplace:

```powershell
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin marketplace remove codex-dynamic-bridge
```

If uninstall returns `restartRequired: true`, the old Sidecar may still be alive inside the current Antigravity process. Fully exit every Antigravity process and launch it again. The final state must show `installed: false`, `enabled: false`, and `endpointReady: false`; `doctor` must report `sidecar.available: false`. A retained runtime-data file may leave `endpointFileExists: true`, which does not mean the Sidecar is available.

Session-link metadata is retained by default at `$CODEX_HOME/plugins/data/codex-dynamic-bridge/links.json`, or at `$HOME/.codex/plugins/data/codex-dynamic-bridge/links.json` when `CODEX_HOME` is unset. Companion runtime data remains under `$HOME/.gemini/antigravity/sidecar_data/codex-dynamic-bridge/codex-bridge/`. For complete cleanup, verify these paths and confirm that no schedule is needed before removing the data manually.

### Troubleshooting

**The `codex plugin` command does not exist**

The installed Codex build does not support plugins. Update Codex Desktop or Codex CLI, then confirm availability with `codex plugin --help` before installing.

**Marketplace download fails or its name conflicts**

Confirm that GitHub is reachable, then run `codex plugin marketplace list` to check whether another source already uses the `codex-dynamic-bridge` name. Remove the old source before adding this repository only when you have confirmed it is no longer needed.

**Antigravity DevTools port file was not found**

Ensure Antigravity is running and check that `%APPDATA%\Antigravity\DevToolsActivePort` exists. The plugin does not start Antigravity itself.

If the file exists but connection still fails, restart Antigravity so it refreshes the port file, and confirm that the port is listening only on a loopback address.

**Control mode reports that Playwright is missing**

First ask Codex, or the same terminal, to print the active interpreter:

```powershell
python -c "import sys; print(sys.executable)"
```

Stay in the same terminal and use the same `python` whose path was just printed to install and verify Playwright; do not switch to another Python launcher:

```powershell
python -m pip install playwright
python -c "import playwright; print('Playwright OK')"
```

**Multiple sessions were found**

Run `discover`, then run `control inspect` for each candidate. Choose the only session with `hasFocus: true`, or pass a conversation ID explicitly.

**The client is running with no conversation / supplements remain queued / approval events are missing**

With no conversation, run `discover-pages` and create the first task from the single trusted shell with `conversation open-new`. During execution, use `conversation send-now`; it associates the message with `Send Now` and verifies that its queue item disappears. The approval path requires a global Companion install and one Antigravity restart; actively run `event wait-approval` after task submission.

**The selector is not unique**

Use a more specific selector. Use `--nth <zero-based-index>` only after confirming what the order of multiple matches means.

**Commands stop working after an Antigravity update**

Antigravity's DOM may change. Re-observe the page with `control inspect` and read-only commands before updating selectors.

### Development

```powershell
git clone https://github.com/GuraQwQ/codex-dynamic-bridge.git
Set-Location .\codex-dynamic-bridge\plugins\codex-dynamic-bridge
python -m bridge.self_test
```

Repository layout:

```text
.
|-- .agents/plugins/marketplace.json
|-- plugins/codex-dynamic-bridge/
|   |-- .codex-plugin/plugin.json
|   |-- bridge/
|   |   |-- cli.py
|   |   |-- control.py
|   |   |-- runtime.py
|   |   |-- state.py
|   |   `-- self_test.py
|   |-- companion/
|   |   |-- install.py
|   |   `-- antigravity-plugin/
|   |-- skills/codex-dynamic-bridge/SKILL.md
|   `-- README.md
|-- LICENSE
`-- README.md
```

Run checks:

```powershell
Set-Location .\plugins\codex-dynamic-bridge
python -m compileall -q bridge
python -m bridge.self_test
Set-Location .\companion\antigravity-plugin\sidecars\codex-bridge
python .\self_test.py
```

When opening an issue, include the Codex version, Python version, Antigravity version, command, and complete error. Remove session content, tokens, and sensitive local paths first.

## License

MIT. See [LICENSE](LICENSE).
