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
```

需要 Sidecar 而 `doctor` 报告不可用时，先只读检查全局注册状态：

```powershell
python -m bridge.cli companion status
```

只有用户明确授权安装后，才执行一次性全局注册。未指定项目时使用 Antigravity 官方默认项目 `default-cli-project`；用户明确给出其他项目 ID 时再传 `--project-id`：

```powershell
python -m bridge.cli companion install-global --confirm-install
python -m bridge.cli companion install-global --project-id <project-id> --confirm-install
```

安装器会自动检测 Antigravity 是否正在运行，并使用官方全局插件目录；不要为每个工作区重复安装，也不要对 Electron 进程做热注入。返回 `restartRequired: true` 时提示用户在完成当前工作后重启一次，不要代替用户关闭或重启应用。

用户明确要求卸载全局 Companion 时执行：

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
```

卸载结果为 `restartRequired: true` 时，说明旧 Sidecar 可能仍在当前 Antigravity 进程内；提示用户完全退出后重新启动。最终只读复查应为 `installed: false`、`enabled: false`、`endpointReady: false`。运行数据默认保留，不要擅自删除计划任务数据。

按以下顺序选择后端：

1. Companion Sidecar：新建会话、发送消息、事件、等待和计划任务。
2. `agy`：指定项目、模型、effort 的结构化 headless 会话。
3. Desktop CDP：当前页面读取、可访问性快照和语义 UI 控制。

写操作一旦开始，不要因失败自动切换后端重试；错误可能发生在后置验证阶段，而动作本身已经生效。

## 选择桌面会话

```powershell
python -m bridge.cli discover
python -m bridge.cli control inspect --id <id>
```

多个候选且用户指的是“当前会话”时，逐个检查。优先选择唯一 `hasFocus: true` 的页面；零个或多个页面报告焦点时询问用户，不要猜测。

## 只读操作

用户明确要求读取、总结或检查会话正文时，该请求本身即授权读取所选会话的完整可见文本：

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

## 会话、模型和项目

结构化新建或继续会话：

```powershell
python -m bridge.cli conversation new --prompt-stdin --project-id <project> --model <slug> --effort high --confirm-create
python -m bridge.cli conversation send --conversation-id <id> --prompt-stdin --confirm-send
python -m bridge.cli conversation wait --conversation-id <id>
```

桌面工作流：

```powershell
python -m bridge.cli conversation open-new --id <id> --confirm-conversation
python -m bridge.cli conversation switch --id <id> --target <title> --confirm-conversation
python -m bridge.cli conversation rename --id <id> --name <name> --confirm-conversation
python -m bridge.cli conversation fork --id <id> --project-id <project> --confirm-conversation
python -m bridge.cli conversation cancel --id <id> --confirm-conversation
python -m bridge.cli model set --id <id> --model <visible-name> --confirm-model
python -m bridge.cli project open --id <id> --name <project> --confirm-project
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
python -m bridge.cli task link --conversation-id <id> --codex-task-id <id> --project-id <id>
python -m bridge.cli task list
```

事件与任务状态只保存白名单字段；不要把会话正文、Hook 原始 payload、令牌或凭据写入链接和任务账本。

## 低层语义控制

仅在领域命令不覆盖目标时使用：

```powershell
python -m bridge.cli control click-role --id <id> --role button --name <name> --confirm-control
python -m bridge.cli control fill-role --id <id> --role textbox --name <name> --text-stdin --confirm-control
python -m bridge.cli control select-role --id <id> --role combobox --name <name> --value <value> --confirm-control
python -m bridge.cli control shortcut --id <id> --key Control+N --confirm-control
```

语义目标默认必须唯一匹配。只有已经观察到多个匹配并能说明序号含义时才用 `--nth`。

## 边界

- 只连接本机回环 CDP 和带随机令牌的 `127.0.0.1` Sidecar。
- 不启动、关闭或登录 Antigravity。
- 不开放任意 JavaScript、任意导航、关闭页面、文件上传或下载。
- 不直接编辑 Antigravity 内部 `app_storage.json`、凭据或私有会话存储。
- `click/fill/press` 使用 `--confirm-control`；会话、模型、项目、设置、产物和计划任务使用各自的专用确认参数。
- 不读取无关会话、账号凭据或模型内部状态。
- 缺少 Playwright、`agy` 或 Sidecar 时报告能力缺口，不自动安装依赖。
- 全局 Companion 安装和卸载分别需要 `--confirm-install` 与 `--confirm-uninstall`；不得仅凭 Sidecar 缺失就自动修改 Antigravity 配置。
