# Codex Dynamic Bridge

[中文](README.md) | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

Let Codex discover, read, and operate Antigravity conversations. Desktop features use Antigravity's existing local CDP endpoint. The official `agy` CLI and Companion Sidecar provide structured tasks, events, approval notifications, and schedules.

## Features

- Read conversations, inspect file changes and artifacts, and follow tool and subagent activity.
- Create or continue tasks, select models and projects, and inject supplements into a running conversation.
- Receive Hook events, wait for completion, and inspect and respond to the current approval.
- Keep submission receipts and review evidence, with separate delivery, execution, and acceptance states.

The main target is Windows 10/11 with Python 3.10+, a plugin-capable Codex build, and Antigravity installed. Desktop control needs Python Playwright; a separate Chromium installation is unnecessary. Use `doctor` to discover the capabilities available on your machine.

## Install the Codex side

Choose the installation location first. `CODEX_HOME` controls Codex plugin caches and the managed `agy` location. For example, select a non-system drive:

```powershell
$env:CODEX_HOME = 'F:\.codex'
[Environment]::SetEnvironmentVariable('CODEX_HOME', 'F:\.codex', 'User')
```

Restart Codex after changing the environment. Plan any migration of existing Codex data separately; setting the variable does not move old data.

Check the Python version and plugin command, then install:

```powershell
python --version
codex plugin --help
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

Start a new Codex conversation to load the skill, then verify it with a read-only request:

```text
Use Codex Dynamic Bridge to list the Antigravity conversations currently discoverable.
```

## Install the Antigravity side

For full task control, Hook events, and approval notifications, send this in a Codex conversation with the plugin loaded:

```text
Use Codex Dynamic Bridge to complete this task. If full capabilities are not loaded, install the official agy and register the global Companion for default-cli-project; I authorize this setup.
```

The agent checks status before running the idempotent installer. On Windows, managed `agy` defaults to `$CODEX_HOME/tools/agy` and is verified against the official HTTPS manifest with SHA-512. A system-drive destination needs explicit authorization. Companion uses Antigravity's official global directory `$HOME/.gemini/config/plugins/codex-dynamic-bridge`; the authorization includes the corresponding configuration changes.

If an existing Companion needs an update while its instance is running, finish the current work, exit that Antigravity instance, and repeat installation. When `restartRequired: true` is returned, restart Antigravity to load the new Hooks, then check `companion status` and `doctor`. The installer does not close the app automatically.

Desktop control also needs Playwright in the Python environment Codex actually uses. Confirm that the interpreter and dependency destination use your chosen drive, then follow the [installation guide](plugins/codex-dynamic-bridge/README.en.md#installation-and-environment).

## Use

```text
Read and summarize the work log in the currently focused Antigravity conversation.
```

```text
Supervise the file changes and test results belonging to the current Antigravity conversation.
```

```text
Send this supplement immediately to the currently running Antigravity conversation; I authorize this send: <supplement>
```

Describe the goal in normal language. CLI results use compact JSON by default; use the global `--verbose` option for diagnostics. Read long conversations incrementally using the message cursor.

## Documentation and downloads

- [Full manual](plugins/codex-dynamic-bridge/README.en.md): installation, backends, commands, updates, removal, and troubleshooting.
- [Supervision lifecycle](plugins/codex-dynamic-bridge/skills/codex-dynamic-bridge/references/supervision.md): submission receipts, event synchronization, and review states.
- [Latest release and downloads](https://github.com/GuraQwQ/codex-dynamic-bridge/releases/latest): release notes and the actual downloadable assets.

The first public release is **0.6.0**. Download `codex-dynamic-bridge-0.6.0.zip` and extract it to a non-system drive. Its root contains `.agents/plugins/marketplace.json` and `plugins/`. Install from the local archive:

```powershell
codex plugin marketplace add F:\tools\codex-dynamic-bridge-0.6.0
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

Replace the example with the absolute extracted root. The GitHub and local sources use the same marketplace name; inspect the registered source before switching. Run subsequent Companion setup from the extracted `plugins/codex-dynamic-bridge` directory. See [Development](plugins/codex-dynamic-bridge/README.en.md#development) for source verification.

MIT. See [LICENSE](LICENSE).
