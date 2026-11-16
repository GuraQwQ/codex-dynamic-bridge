# Codex Dynamic Bridge

[中文](README.md) | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

让 Codex 发现、读取和操作 Antigravity 会话。桌面功能连接 Antigravity 已开放的本机 CDP；官方 `agy` 和 Companion Sidecar 提供结构化任务、事件、审批通知及计划任务。

## 可以做什么

- 读取当前会话、查看文件变更与产物，跟进工具和子 Agent 活动。
- 新建或继续任务、选择模型与项目，向执行中的会话立即补充内容。
- 接收 Hook 事件、等待完成、检查并响应当前审批。
- 保存投递回执与验收证据，区分消息已接受、执行停止和验收通过。

主要面向 Windows 10/11，需要 Python 3.10+、支持插件的 Codex，以及已安装的 Antigravity。桌面控制需要 Python Playwright；无需另装 Chromium。具体能力以 `doctor` 的本机探测结果为准。

## 安装 Codex 端

先确定安装位置。`CODEX_HOME` 控制 Codex 插件缓存及托管 `agy` 的位置；例如将它设置到非系统盘：

```powershell
$env:CODEX_HOME = 'F:\.codex'
[Environment]::SetEnvironmentVariable('CODEX_HOME', 'F:\.codex', 'User')
```

环境变量变更后重新启动 Codex。已有 Codex 数据如需迁移，应单独安排迁移；设置变量不会搬移旧数据。

确认版本和插件命令可用，再安装：

```powershell
python --version
codex plugin --help
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

安装后新建 Codex 会话，让新技能加载。先用只读请求验证：

```text
使用 Codex Dynamic Bridge 列出 Antigravity 当前可发现的会话
```

## 安装 Antigravity 端

需要完整任务控制、Hook 事件和审批通知时，在已加载插件的 Codex 会话中发送：

```text
使用 Codex Dynamic Bridge 完成此任务；如果完整能力尚未装载，自动安装官方 agy 并注册全局 Companion 到 default-cli-project，我授权此次装载
```

Agent 会先检查状态，再运行幂等安装。Windows 上托管 `agy` 默认位于 `$CODEX_HOME/tools/agy`，下载后按官方 HTTPS 清单校验 SHA-512；系统盘目标需另行明确授权。Companion 注册到 Antigravity 官方全局目录 `$HOME/.gemini/config/plugins/codex-dynamic-bridge`，该授权包含对应配置写入。

已有 Companion 需要更新且实例仍运行时，先完成工作并退出对应 Antigravity 实例，再重复安装。返回 `restartRequired: true` 时，重启 Antigravity 使新 Hook 生效；随后检查 `companion status` 和 `doctor`。安装器不会自动关闭应用。

桌面控制还需要将 Playwright 安装到 Codex 实际使用的 Python。先确认解释器和依赖目标位于你选择的磁盘，再按[详细安装说明](plugins/codex-dynamic-bridge/README.md#安装与环境)操作。

## 使用

```text
读取并总结 Antigravity 当前聚焦会话的工作记录
```

```text
作为监工检查 Antigravity 当前会话对应的文件变更和测试结果
```

```text
把以下补充立即发送到当前正在执行的 Antigravity 会话，我授权此次发送：<补充内容>
```

普通使用直接描述目标即可。CLI 默认输出紧凑 JSON；需要诊断时可使用全局 `--verbose`。长会话可按消息游标增量读取，避免反复读取全部正文。

## 文档与下载

- [完整操作手册](plugins/codex-dynamic-bridge/README.md)：安装、后端选择、命令、更新、卸载与排障。
- [监工生命周期](plugins/codex-dynamic-bridge/skills/codex-dynamic-bridge/references/supervision.md)：投递回执、事件同步及验收状态。
- [最新 Release 与发布包](https://github.com/GuraQwQ/codex-dynamic-bridge/releases/latest)：版本说明及可下载资产；安装包内容以该页为准。

首个公开版本为 **0.6.0**。下载 `codex-dynamic-bridge-0.6.0.zip`，解压到非系统盘；解压根包含 `.agents/plugins/marketplace.json` 和 `plugins/`。从本地发布包安装：

```powershell
codex plugin marketplace add F:\tools\codex-dynamic-bridge-0.6.0
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

将示例替换为实际解压根的绝对路径。GitHub 与本地包的 marketplace 同名，切换来源前先核对已有注册。Companion 的后续安装从解压目录的 `plugins/codex-dynamic-bridge` 运行。源码验证见[开发说明](plugins/codex-dynamic-bridge/README.md#开发)。

MIT，详见 [LICENSE](LICENSE)。
