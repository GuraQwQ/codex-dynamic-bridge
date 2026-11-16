# Antigravity Companion

[中文操作手册](../README.md#完整能力装载) · [English manual](../README.en.md#full-setup)

Antigravity 端的 Companion 源码位于 `antigravity-plugin/`。它提供官方 `agentapi` 调用、会话生命周期 Hook、审批事件及周期任务。

从上一级 Codex 插件目录运行全局安装：

```powershell
python -m bridge.cli companion status
python -m bridge.cli companion install-global --confirm-install
```

默认项目为 `default-cli-project`。全局目录、安装授权、更新时的实例状态、重启确认、卸载及运行数据保留说明统一维护在操作手册中。
