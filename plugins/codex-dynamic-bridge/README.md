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
codex plugin list --marketplace codex-dynamic-bridge --available
```

4. 新建 Codex 任务以加载新技能。已有任务不会自动重新加载刚安装的技能。

   - Codex Desktop：点击新建任务。
   - Codex CLI：结束当前交互会话，然后重新运行 `codex`。

5. 在新任务中发送以下只读烟雾测试：

```text
使用 Codex Dynamic Bridge 列出 Antigravity 当前可发现的会话；不要修改页面
```

预期结果是会话 JSON 列表；若 Antigravity 未运行，应收到明确的调试端口文件错误，而不是执行页面修改。

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

### 命令参考

以下开发命令应在 `plugins/codex-dynamic-bridge` 目录中运行。正常使用时应直接让 Codex 调用插件技能。

```powershell
# 发现会话
python -m bridge.cli discover

# 检查页面状态
python -m bridge.cli control inspect --id <conversation-id>

# 读取完整可见文本，默认读取 body
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

- `discover`、`inspect`、`read`、`get` 和 `wait` 是只读操作。
- 用户明确要求读取、总结或检查会话正文时，插件可读取所选会话的完整可见文本，无需额外选择器。
- `click`、`fill` 和 `press` 只有在用户明确授权当前具体动作后才能使用 `--confirm-control`。
- 插件不提供任意 JavaScript、任意导航、关闭页面、文件上传或下载命令。
- 插件只连接 `127.0.0.1`、`localhost` 或 `::1` 上的 Antigravity 会话。
- 页面修改命令返回错误并不能证明页面完全没有发生变化，因此插件不会自动重试写操作。
- 插件不会主动读取无关会话、账号凭据或模型内部状态。

### 更新

```powershell
codex plugin marketplace upgrade codex-dynamic-bridge
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

移除后重新安装可确保使用刷新后的 marketplace 快照。更新后新建 Codex 任务。

### 卸载

```powershell
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin marketplace remove codex-dynamic-bridge
```

插件的会话链接元数据默认保留在 `$CODEX_HOME/plugins/data/codex-dynamic-bridge/links.json`；未设置 `CODEX_HOME` 时位于 `$HOME/.codex/plugins/data/codex-dynamic-bridge/links.json`。需要完全清理时，在确认路径无误后手动删除该文件。

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
|   |   `-- self_test.py
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
codex plugin list --marketplace codex-dynamic-bridge --available
```

4. Start a new Codex task so the newly installed skill is loaded. Existing tasks do not automatically reload newly installed skills.

   - Codex Desktop: create a new task.
   - Codex CLI: end the current interactive session, then run `codex` again.

5. Run this read-only smoke test in the new task:

```text
Use Codex Dynamic Bridge to list currently discoverable Antigravity sessions; do not modify the page.
```

The expected result is a JSON session list. If Antigravity is not running, expect a clear DevTools port-file error rather than a page mutation.

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

### Command reference

Run the following development commands from `plugins/codex-dynamic-bridge`. For normal use, ask Codex to invoke the plugin skill instead.

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

- `discover`, `inspect`, `read`, `get`, and `wait` are read-only operations.
- When the user explicitly asks to read, summarize, or inspect session content, the plugin may read the complete visible text of the selected session without requiring another selector.
- `click`, `fill`, and `press` may use `--confirm-control` only after the user authorizes that specific action.
- The plugin does not expose arbitrary JavaScript, arbitrary navigation, page closing, file upload, or download commands.
- It only connects to Antigravity sessions on `127.0.0.1`, `localhost`, or `::1`.
- An error from a mutating command does not prove that the page remained unchanged, so the plugin never retries write actions automatically.
- The plugin does not proactively read unrelated sessions, account credentials, or model-internal state.

### Update

```powershell
codex plugin marketplace upgrade codex-dynamic-bridge
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

Removing and reinstalling ensures that Codex uses the refreshed marketplace snapshot. Start a new Codex task after updating.

### Uninstall

```powershell
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin marketplace remove codex-dynamic-bridge
```

Session-link metadata is retained by default at `$CODEX_HOME/plugins/data/codex-dynamic-bridge/links.json`, or at `$HOME/.codex/plugins/data/codex-dynamic-bridge/links.json` when `CODEX_HOME` is unset. For a complete cleanup, manually remove that file only after verifying the path.

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
|   |   `-- self_test.py
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
```

When opening an issue, include the Codex version, Python version, Antigravity version, command, and complete error. Remove session content, tokens, and sensitive local paths first.

## License

MIT. See [LICENSE](LICENSE).
