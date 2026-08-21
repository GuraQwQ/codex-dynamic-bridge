---
name: codex-dynamic-bridge
description: 发现 Antigravity 本地会话、维护任务链接元数据，或在用户明确授权后通过 CDP 检查、等待、点击、填充和按键控制指定会话。当用户要求查看、同步、桥接或操作 Antigravity 会话时使用。
---

# Codex Dynamic Bridge

从本文件所在目录向上两级得到插件根目录。所有命令都在插件根目录使用系统 Python 运行 `bridge/cli.py`，不要假设存在全局可执行命令。

## 分阶段选择会话

先只读发现候选：

```powershell
python -m bridge.cli discover
```

控制动作必须传入 conversation ID 或 DevTools id。多个候选且用户指的是“当前会话”时，逐个只读检查：

```powershell
python -m bridge.cli control inspect --id <id>
```

优先选择唯一返回 `hasFocus: true` 的页面；零个或多个页面报告焦点时询问用户，不要猜测。

## 只读控制

观察目标前先检查页面。用户明确要求读取、总结或检查会话正文时，该请求本身即授权只读获取当前会话的完整可见文本，无需用户另行提供选择器：

```powershell
python -m bridge.cli control read --id <id>
```

需要缩小读取范围或检查元素状态时，再使用明确选择器：

```powershell
python -m bridge.cli control read --id <id> --selector <selector>
python -m bridge.cli control get --id <id> --selector <selector>
python -m bridge.cli control wait --id <id> --selector <selector> --state visible
```

`read` 默认读取 `body` 的完整可见文本。选择器默认必须唯一匹配；只有已经观察到多个匹配且能说明序号含义时，才使用 `--nth <zero-based-index>`。

## 页面修改

`click`、`fill` 和 `press` 会改变页面。只有用户已经明确授权当前具体动作和目标时，才能传入 `--confirm-control`：

```powershell
python -m bridge.cli control click --id <id> --selector <selector> --confirm-control
python -m bridge.cli control fill --id <id> --selector <selector> --text <text> --confirm-control
python -m bridge.cli control press --id <id> --selector <selector> --key <key> --confirm-control
```

敏感或多行文本优先使用 `--text-stdin`。不要仅因用户安装或启用控制模式，就推定用户授权了发送消息、删除内容、提交表单或其他具体操作。

动作返回后，用 `get`、`wait` 或 `inspect` 验证后置状态。若命令报错，如实说明动作可能已部分发生；不要自动重试页面修改。

## 会话链接

保存会话元数据：

```powershell
python -m bridge.cli live --id <id>
```

列出、合并或删除链接：

```powershell
python -m bridge.cli list
python -m bridge.cli sync --source <json-path>
python -m bridge.cli remove --id <link-id>
```

## 边界

- 只连接 Antigravity 已暴露的本机回环 CDP 端口，不启动、关闭或登录客户端。
- 不开放任意 JavaScript、任意导航、关闭页面、文件上传或下载。
- 用户明确要求读取、总结或检查会话正文时，可以读取所选当前会话的完整可见文本，不要求用户提供选择器；不要读取无关会话、账号凭据或模型内部状态。
- DOM 和选择器可能变化，每次页面发生显著变化后都重新观察。
- 控制模式需要 Python Playwright；插件不自动安装依赖。缺失时报告错误，不影响元数据命令。
