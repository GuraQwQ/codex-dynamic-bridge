# Antigravity Companion

该伴生插件为 Codex Dynamic Bridge 提供稳定的本机 Sidecar 后端：

- 使用 Antigravity 官方 `agentapi` 新建会话和发送消息。
- 通过 Hook 接收会话生命周期事件，并在 `run_command`/`ask_permission` 前上报审批请求。
- 管理最小间隔为 60 秒的本地定时任务。
- 仅绑定 `127.0.0.1`，每次启动生成随机令牌；普通会话请求不额外记录提示词。
- 定时任务必须把提示词保存在 Sidecar 私有 `data/schedules.json` 中，但不会持久化模型回复正文。

源码位于 `antigravity-plugin/`。推荐从 Codex 插件根目录一次性完成全局注册：

```powershell
python -m bridge.cli companion status
python -m bridge.cli companion install-global --confirm-install
```

安装器会自动检测正在运行的 Antigravity，把文件原子替换到官方全局目录
`~/.gemini/config/plugins/codex-dynamic-bridge`，并在 `~/.gemini/config/config.json`
中合并 `codex-dynamic-bridge/codex-bridge` 的 `enabled` 与 `projectId`。默认使用官方
`default-cli-project`；其他配置保持不变；
以后打开任何工作区都不需要重复安装。文件和配置相同时直接返回 `updated: false`；
已有 Companion 需要更新但 Antigravity 正在运行时，安装器保持零修改并要求完全退出后重试。

卸载命令只删除本插件和对应 Sidecar 配置项：

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
```

Hook 命令需要指向 `event_sink.py` 和 Sidecar 运行数据目录中的 `endpoint.json` 绝对路径。
不要把 `endpoint.json` 或其中的令牌提交到 Git。

`PreToolUse` 只匹配 `run_command|ask_permission`，返回 Antigravity 官方 `ask` 决策。即使事件上报失败也不会返回 `allow`，因此不会绕过原有权限提示。事件仅保留 conversation ID、工具名、审批状态等白名单字段，不传输或保存完整命令参数。安装或更新 Hook 后需要重启一次 Antigravity；随后 Codex 可运行：

```powershell
python -m bridge.cli event wait-approval --conversation-id <id> --tool-name run_command
python -m bridge.cli approval inspect --id <id>
```

## English

The source is under `antigravity-plugin/`. Register it globally once from the Codex plugin root:

```powershell
python -m bridge.cli companion status
python -m bridge.cli companion install-global --confirm-install
```

The installer detects a running Antigravity instance, atomically replaces the files under the
official global directory `~/.gemini/config/plugins/codex-dynamic-bridge`, and merges only the
`codex-dynamic-bridge/codex-bridge` entry in `~/.gemini/config/config.json`, using the documented
`default-cli-project` by default. Every workspace then
shares this Companion. Identical files and configuration return `updated: false`. If an installed
Companion needs an update while Antigravity is running, the installer makes no changes and asks the
user to fully exit before retrying.

Uninstall removes only this plugin and its Sidecar entry:

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
```

Hook commands use absolute paths to `event_sink.py` and the runtime `endpoint.json`. Never commit
`endpoint.json` or its token.

`PreToolUse` matches only `run_command|ask_permission` and returns Antigravity's official `ask`
decision. A reporting failure never changes this to `allow`, so the Hook cannot bypass the normal
permission prompt. Only allowlisted fields such as conversation ID, tool name, and approval state
are transmitted and persisted; complete command arguments are not. Restart Antigravity once after
installing or updating the Hook, then Codex can use `event wait-approval` and `approval inspect`.
