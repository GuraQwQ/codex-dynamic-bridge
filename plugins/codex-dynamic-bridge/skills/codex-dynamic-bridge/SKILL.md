---
name: codex-dynamic-bridge
description: 发现、读取、创建和管理 Antigravity 会话、模型、项目、设置、产物、计划任务、后台活动及 Codex 任务链接；优先使用官方 agy/agentapi 后端，必要时通过本机 CDP 语义控制桌面。当用户要求查看、同步、桥接或操作 Antigravity 时使用。
---

# Codex Dynamic Bridge

从本文件所在目录向上两级得到插件根目录。所有命令都在插件根目录运行：

```powershell
python -m bridge.cli <command>
```

## 先探测能力

每个新任务先运行：

```powershell
python -m bridge.cli doctor
python -m bridge.cli setup status
```

需要 Sidecar 而 `doctor` 报告不可用时，先只读检查全局注册状态：

```powershell
python -m bridge.cli companion status
```

当用户明确要求“使用 Codex Dynamic Bridge 完成任务”并授权自动装载完整能力时，执行一次：

```powershell
python -m bridge.cli setup ensure --confirm-setup
```

该命令在 Windows 从官方 HTTPS manifest 下载 `agy`，校验可信域名与 SHA-512 后安装到 `$CODEX_HOME/tools/agy`，并全局注册 Companion。不得因普通发现、只读或状态检查自动安装。只需要 Companion 时，用户明确授权安装后执行一次性全局注册。未指定项目时使用 Antigravity 官方默认项目 `default-cli-project`；用户明确给出其他项目 ID 时再传 `--project-id`：

```powershell
python -m bridge.cli companion install-global --confirm-install
python -m bridge.cli companion install-global --project-id <project-id> --confirm-install
```

安装器会自动检测 Antigravity 是否正在运行，并使用官方全局插件目录；相同文件和配置会直接返回 `updated: false`。已有 Companion 需要更新但 Antigravity 正在运行时，安装器会零修改拒绝并要求完全退出后重试；不要为每个工作区重复安装，也不要对 Electron 进程做热注入。返回 `restartRequired: true` 时提示用户在完成当前工作后重启一次，不要代替用户关闭或重启应用。

用户明确要求卸载全局 Companion 时执行：

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
```

卸载结果为 `restartRequired: true` 时，说明旧 Sidecar 可能仍在当前 Antigravity 进程内；提示用户完全退出后重新启动。最终只读复查应为 `installed: false`、`enabled: false`、`endpointReady: false`。运行数据默认保留，不要擅自删除计划任务数据。

按以下顺序选择后端：

1. Companion Sidecar：新建会话、发送消息、事件、等待和计划任务。
2. `agy`：指定项目、模型、effort 的结构化 headless 会话。
3. Desktop CDP：当前页面读取、可访问性快照和语义 UI 控制。

如果用户要求监工或中途追加，且目标桌面会话正在运行，必须使用 `conversation send --backend auto` 或 `conversation send-now`；`auto` 会检测到桌面页面后强制走 `Send Now`，不能改用 Sidecar 排队。只有用户明确指定 `--backend sidecar` 或目标没有桌面页面时，才使用 Sidecar/`agy`。Companion/`agy` 适合不需要实时干预的 headless 任务。

写操作一旦开始，不要因失败自动切换后端重试；错误可能发生在后置验证阶段，而动作本身已经生效。

## 选择桌面会话

```powershell
python -m bridge.cli discover
python -m bridge.cli discover-pages
python -m bridge.cli control inspect --id <id>
```

多个候选且用户指的是“当前会话”时，逐个检查。优先选择唯一 `hasFocus: true` 的页面；零个或多个页面报告焦点时询问用户，不要猜测。若 `discover` 为空但 `discover-pages` 返回唯一可信外壳页，可直接用带提示词的 `conversation open-new` 创建第一个任务，不要求用户先手工打开会话。

## 只读操作

用户明确要求读取、总结或检查会话正文时，该请求本身即授权读取所选会话的可见文本。`control read`/`snapshot` 省略选择器时只读取 User/Agent 消息 article 和相关控件，自动排除侧栏、项目列表、菜单、历史会话和无关 DOM；只有用户显式传入 `--selector body` 才读取整页：

```powershell
python -m bridge.cli control read --id <id>
python -m bridge.cli control snapshot --id <id>
python -m bridge.cli settings read --id <id>
python -m bridge.cli model desktop-list --id <id>
python -m bridge.cli usage --id <id>
python -m bridge.cli activity --conversation-id <id>
python -m bridge.cli artifact list --conversation-id <id>
```

可访问性快照比长期 CSS 选择器稳定。页面变化后重新生成快照，不复用旧控件含义。

## 监工模式：变更优先

用户要求 Codex 监工、审查或跟进 Antigravity 的实现时，默认检查实际文件变更，不先读取全部聊天上下文：

1. 从用户给出的路径、任务映射或 Hook 的 `workspacePaths` 确认唯一工作区；不确定时只读列出候选，不猜路径。
2. 第一来源是当前 Antigravity 会话 Review 页归属的文件 diff：`python -m bridge.cli review changes --id <id>`。该命令只返回“评审”区域的可见文本与控件，不读取聊天历史；先按用户最新验收项检查这些文件和测试结果。
3. 再运行该仓库的 `git status --short`、`git diff --stat`、`git diff` 和 `git diff --cached` 做补全与验证，区分会话改动、用户原有改动和其他会话改动。若 Review diff 与当前实现一致，不要求重复修改。若项目有 CodeGraph 配置，优先用 CodeGraph 定位受影响关系。
4. 只有文件变更无法说明意图、出现运行错误/审批、验收项缺少依据，或用户明确要求回顾过程时，才读取所选会话的相关可见上下文；不要默认抓取全部历史。
5. 不得仅因旧对话中的计划与当前实现不同就要求返工。以当前 diff、用户最新要求、可复现验证和明确边界为准，保留已经正确完成的改动。
6. 发现需要补充或修正的事项时，用户授权发送后使用 `conversation send-now` 立即追加到正在执行的会话，不留在队列中等待当前回合结束。

监工默认是只读审查。除非用户明确要求 Codex 直接修改目标仓库，否则不要代替 Antigravity 改文件。

## 会话、模型和项目

结构化新建、继续或恢复会话：

```powershell
python -m bridge.cli conversation new --prompt-stdin --project-id <project> --model <slug> --effort high --confirm-create
python -m bridge.cli conversation send --conversation-id <id> --prompt-stdin --confirm-send
python -m bridge.cli conversation resume --conversation-id <id> --prompt-stdin --confirm-send
python -m bridge.cli conversation wait --conversation-id <id>
```

用户已有项目目录时，使用 `agy` 的工作目录和新项目开关，不能只把本地路径当作 `project-id`：

```powershell
python -m bridge.cli conversation new --prompt-stdin --project-path <absolute-project-directory> --new-project --confirm-create
```

`--project-path` 必须是已存在的目录；插件会把它作为 `agy` 子进程的 `cwd`，不会替用户创建或移动目录。

桌面会话仍在执行时，Codex 的补充信息必须立即注入，不要留到当前回合结束后。用户明确授权发送该补充后执行：

```powershell
python -m bridge.cli conversation send-now --id <id> --prompt-stdin --confirm-send
```

该工作流先把消息加入当前会话，再按正文关联唯一 `Send Now` 按钮并立即发送；会验证对应队列项消失。`conversation send --backend auto` 在发现桌面会话时自动使用同一工作流。命令失败时动作可能已经生效，不得重试。

桌面工作流：

```powershell
python -m bridge.cli project list --id <id>
python -m bridge.cli conversation open-new --project-id <project> --prompt-stdin --confirm-conversation
python -m bridge.cli conversation open-new --prompt-stdin --confirm-conversation
python -m bridge.cli conversation switch --id <id> --target <title> --confirm-conversation
python -m bridge.cli conversation rename --id <id> --name <name> --confirm-conversation
python -m bridge.cli conversation fork --id <id> --project-id <project> --confirm-conversation
python -m bridge.cli conversation cancel --id <id> --confirm-conversation
python -m bridge.cli model set --id <id> --model <visible-name> --confirm-model
python -m bridge.cli project open --id <id> --project-id <project> --confirm-project
python -m bridge.cli project new --id <id> --confirm-project
```

模型在当前回合运行期间切换时，只影响后续消息。发送下一条消息前回读模型菜单验证。

## 设置、产物和计划任务

```powershell
python -m bridge.cli settings set --id <id> --label <accessible-label> --value <value> --confirm-settings
python -m bridge.cli artifact read --conversation-id <id> --path <relative-path>
python -m bridge.cli artifact proceed --id <id> --confirm-artifact
python -m bridge.cli schedule create --prompt-stdin --interval-seconds 3600 --confirm-schedule
python -m bridge.cli schedule list
python -m bridge.cli schedule remove --schedule-id <id> --confirm-schedule
```

权限、沙箱、遥测、外部目录访问和 Remote Control 属于高风险持久设置。执行前复述准确 scope、标签和值，并在动作后回读验证。

## 事件和任务映射

```powershell
python -m bridge.cli event sync
python -m bridge.cli event list --conversation-id <id>
python -m bridge.cli event wait-approval --conversation-id <id> --tool-name run_command --timeout-seconds 300
python -m bridge.cli approval inspect --id <id>
python -m bridge.cli task link --conversation-id <id> --codex-task-id <id> --project-id <id>
python -m bridge.cli task list
```

事件与任务状态只保存白名单字段；不要把会话正文、Hook 原始 payload、令牌或凭据写入链接和任务账本。

投递长任务后主动等待 `PreToolUse` 审批事件，不依赖偶尔读取整页。事件到达后先运行 `approval inspect`，它可返回当前可见命令、选项与提交按钮，但不会持久化命令正文。只有用户明确授权当前可见命令时，才能把事件返回的 `toolName`、`observedAt` 与检查到的精确选项/按钮绑定后响应：

```powershell
python -m bridge.cli approval respond --id <id> --decision allow --option-name <exact-option-text> --button-name <exact-submit-button-name> --tool-name <toolName> --event-observed-at <observedAt> --confirm-approval
```

未授权时只通知用户。`PreToolUse` Hook 返回 `ask`，绝不返回 `allow` 绕过权限；只保存工具名和审批状态，不保存完整命令参数。

## 低层语义控制

仅在领域命令不覆盖目标时使用：

```powershell
python -m bridge.cli control click-role --id <id> --role button --name <name> --confirm-control
python -m bridge.cli control fill-role --id <id> --role textbox --name <name> --text-stdin --confirm-control
python -m bridge.cli control select-role --id <id> --role combobox --name <name> --value <value> --confirm-control
```

语义目标默认必须唯一匹配。只有已经观察到多个匹配并能说明序号含义时才用 `--nth`。

## 边界

- 只连接本机回环 CDP 和带随机令牌的 `127.0.0.1` Sidecar。
- 不启动、关闭或登录 Antigravity。
- 不开放任意 JavaScript、任意导航、关闭页面、文件上传或下载。
- 不直接编辑 Antigravity 内部 `app_storage.json`、凭据或私有会话存储。
- `click/fill/press` 使用 `--confirm-control`；会话、模型、项目、设置、产物和计划任务使用各自的专用确认参数。
- 不读取无关会话、账号凭据或模型内部状态。
- 缺少 Playwright、`agy` 或 Sidecar 时报告能力缺口；只有用户明确要求插件完成任务并授权完整装载时，才可运行 `setup ensure --confirm-setup`。
- 全局 Companion 安装和卸载分别需要 `--confirm-install` 与 `--confirm-uninstall`；不得仅凭 Sidecar 缺失就自动修改 Antigravity 配置。
- 正在运行的会话收到补充信息时使用 `conversation send --backend auto` 或 `conversation send-now`；不要使用默认 Sidecar `conversation send` 让补充留在 `Queued Messages` 等待会话结束。
- 自动批准只适用于用户明确授权的当前可见命令；设置了 Companion 或 `--confirm-approval` 本身不构成批准任意命令的授权。
