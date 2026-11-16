# Codex Dynamic Bridge manual

[中文](README.md) | [English](README.en.md)

[Project home](../../README.en.md) · [Latest release](https://github.com/GuraQwQ/codex-dynamic-bridge/releases/latest) · [License](LICENSE)

This plugin connects to Antigravity's existing local CDP endpoint and optionally uses the official `agy` CLI and Companion Sidecar. Run the CLI examples from this directory. For normal use, ask Codex with the skill loaded to carry out the task.

## Installation and environment

The main target is Windows 10/11, Python 3.10+, a Codex build supporting `codex plugin`, and Antigravity installed. Desktop reads and control require Antigravity to be running with `%APPDATA%\Antigravity\DevToolsActivePort` present. Metadata commands use only the Python standard library.

Choose where tools and dependencies will be installed first. `CODEX_HOME` defaults to `.codex` under your home directory; set a non-system-drive location before installation if needed:

```powershell
$env:CODEX_HOME = 'F:\.codex'
[Environment]::SetEnvironmentVariable('CODEX_HOME', 'F:\.codex', 'User')
python --version
codex plugin --help
codex plugin marketplace add GuraQwQ/codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
codex plugin list --json --marketplace codex-dynamic-bridge --available
```

Restart Codex after changing the environment. This selects the directory for subsequent use; it does not migrate existing data. Start a new Codex conversation after installing the plugin to load its skill.

Desktop commands need Python Playwright. Check the interpreter path and confirm the dependency destination uses your chosen drive, then install into that same Python:

```powershell
python -c "import sys; print(sys.executable)"
python -m pip install --no-cache-dir playwright
python -c "import sys, playwright; print(sys.executable); print('Playwright OK')"
```

A `playwright install` step is unnecessary: the plugin uses Antigravity's running Chromium instance. Choose the Python interpreter, virtual environment, and pip destination separately; `CODEX_HOME` does not control them.

Verify the installation with a read-only request:

```text
Use Codex Dynamic Bridge to list the Antigravity conversations currently discoverable.
```

For a versioned download, get `codex-dynamic-bridge-0.6.0.zip` from the [release page](https://github.com/GuraQwQ/codex-dynamic-bridge/releases/latest) and extract it to a non-system drive. Its root contains `.agents/plugins/marketplace.json` and `plugins/codex-dynamic-bridge/`. Add that extracted root as a local marketplace:

```powershell
codex plugin marketplace add F:\tools\codex-dynamic-bridge-0.6.0
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

Replace the example with the absolute extracted directory. For CLI commands, enter its `plugins/codex-dynamic-bridge` subdirectory. A source checkout uses the same layout. When switching between a GitHub marketplace and a local archive, check `codex plugin marketplace list` for an existing source with the same name before replacing it.

## Full setup

Read-only discovery does not install tools:

```powershell
python -m bridge.cli doctor
python -m bridge.cli setup status
python -m bridge.cli companion status
```

For the official CLI, events, approval notifications, and schedules, authorize setup in Codex:

```text
Use Codex Dynamic Bridge to complete this task. If full capabilities are not loaded, install the official agy and register the global Companion for default-cli-project; I authorize this setup.
```

The agent runs this from the installed plugin root:

```powershell
python -m bridge.cli setup install --confirm-setup
```

On Windows, managed `agy` defaults to `$CODEX_HOME/tools/agy`. Downloads are checked against the official HTTPS manifest, trusted host, and SHA-512 before atomic replacement. System-drive destinations require explicit authorization. Use `--agy-dir F:\tools\agy` for another location; use `--allow-system-drive` only when the user has expressly approved that destination.

Companion registers under `$HOME/.gemini/config/plugins/codex-dynamic-bridge` and merges its own Sidecar entry in `$HOME/.gemini/config/config.json`. This official directory is independent of `CODEX_HOME`; full-setup authorization includes those small configuration changes. The default project is `default-cli-project`.

To install only Companion, or choose another project:

```powershell
python -m bridge.cli companion install-global --confirm-install
python -m bridge.cli companion install-global --project-id <project-id> --confirm-install
```

Identical files, configuration, and Hooks return `updated: false`. If an installed Companion needs an update while Antigravity or its Sidecar is running, finish the current work, exit that instance, and repeat installation. When `restartRequired: true` is returned, restart Antigravity, then recheck `companion status` and `doctor`. The installer does not close the app automatically.

Updates and download-cache cleanup use the same entry point:

```powershell
python -m bridge.cli setup status --check-update
python -m bridge.cli setup install --refresh-agy --confirm-setup
python -m bridge.cli setup clean --confirm-clean
```

Externally managed `agy` is preserved by default; explicitly pass `--agy-dir` to select a different installation. `pending: true` means a busy file retained verified staging data for retry; it does not mean the update finished. `setup clean` removes only this installer's download cache. Manifest and digest updates currently support Windows. The Unix branch uses the official installation script and returns `updateSkipped` when refreshing an existing managed executable.

## Backends and output

| Backend | Purpose |
| --- | --- |
| Desktop CDP | Visible messages, accessibility snapshots, model/project UI, and desktop workflows |
| Official `agy` | Structured headless tasks with project, model, effort, and agent options |
| Companion Sidecar | Official `agentapi` calls, Hook events, completion waits, and schedules |

`doctor` reports actual capabilities and the installed `agy` version and options. A plain `conversation send --backend auto` uses the desktop send workflow when the target page is found: it verifies a direct send when idle or immediate delivery of a queued supplement when running. Model, effort, or agent options and `resume` select `agy`, which supports those parameters. Once a write starts, a failure does not trigger automatic resending through another backend; verification can fail after the send has already taken effect.

The CLI returns one compact JSON result by default, retaining IDs, business results, states, and receipts. Successful workflows omit before/after page snapshots. Explicit `inspect`/`snapshot` requests keep their requested details; errors and unknown outcomes keep diagnostics. Put the global `--verbose` option before the subcommand:

```powershell
python -m bridge.cli --verbose control inspect --id <id>
```

The Codex model does not determine which tools the host exposes. Native computer control and asynchronous waits are optional enhancements. `--model` selects the Antigravity execution model.

## Discovery and incremental reads

```powershell
python -m bridge.cli discover
python -m bridge.cli discover-pages
python -m bridge.cli control inspect --id <id>
python -m bridge.cli control read --id <id>
python -m bridge.cli control snapshot --id <id>
python -m bridge.cli control read --id <id> --after-message <nextMessageIndex> --limit 5
```

With multiple conversations, inspect each candidate's `hasFocus`, select the single focused page, or explicitly provide its ID. If no conversation exists but there is one trusted client shell, `conversation open-new` can create the first task.

Default reads target the conversation messages and exclude sidebars, history, and menus. Incremental reads return `nextMessageIndex`. While `running: true`, the last message stays at the cursor so the growing response can be read again. Use `--selector body` only when whole-page content is needed, or narrow the region with `--selector "main"`.

## Create, send, and resume

Prefer UTF-8 standard input for prompts. Files below are development examples; the agent can pass user text directly without asking the user to create files. PowerShell 7 is recommended:

```powershell
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation open-new --prompt-stdin --confirm-conversation
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation open-new --project-id <project-id> --prompt-stdin --confirm-conversation
Get-Content -Raw -Encoding UTF8 .\supplement.txt | python -m bridge.cli conversation send-now --id <id> --prompt-stdin --confirm-send
Get-Content -Raw -Encoding UTF8 .\supplement.txt | python -m bridge.cli conversation send --conversation-id <id> --backend auto --prompt-stdin --confirm-send
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation resume --conversation-id <id> --prompt-stdin --confirm-send
python -m bridge.cli conversation wait --conversation-id <id>
```

Headless tasks support project, model, and effort selection. A local directory can also become a new project:

```powershell
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation new --project-id <project-id> --model <slug> --effort high --prompt-stdin --confirm-create
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation new --project-path F:\work\my-project --new-project --prompt-stdin --confirm-create
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli conversation new --backend agy --stream --project-id <project-id> --prompt-stdin --confirm-create
```

A streaming `init` event immediately binds the conversation and receipt. Progress is persisted as it arrives; Bridge stdout remains the final JSON result. `turn_completed` means the CLI turn returned. `stopped` needs a fully idle Hook. Neither state proves acceptance.

Desktop conversation actions:

```powershell
python -m bridge.cli conversation switch --id <id> --target <title> --confirm-conversation
python -m bridge.cli conversation rename --id <id> --name <name> --confirm-conversation
python -m bridge.cli conversation fork --id <id> --project-id <project-id> --confirm-conversation
python -m bridge.cli conversation cancel --id <id> --confirm-conversation
```

## Models, projects, settings, and artifacts

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

## Supervision and events

Start supervision with the current conversation's Review changes, then use the corresponding repository's `git status`, `git diff`, and test results to complete the picture. Prefer Hook `workspacePaths` for the workspace, or a user-provided path when absent; inspect multiple candidates before choosing. Read relevant messages when they are needed to explain changes, errors, or approvals.

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

Submission receipts are saved before sending. `conversation wait` / `event wait` use the latest receipt boundary by default; explicitly pass `--after <observedAt>` when needed. `event sync` checkpoints pages by endpoint and conversation scope. A retry replays incomplete pages and deduplicates them. `eventStreamCursor: true` in `doctor` indicates incremental cursor support.

`accepted`, observed execution stopping, and review `passed` are separate states. `task record-review` stores the supervisor's verdict and evidence-file digest; it does not run tests. `activity.agents` lists observed parent/child relationships, roles, and actual workspaces. See the [supervision lifecycle](skills/codex-dynamic-bridge/references/supervision.md) for state and recovery details.

## Approvals and schedules

Companion's `PreToolUse` matches `run_command|ask_permission` and returns Antigravity's `ask` decision. Restart Antigravity after installing or updating Hooks, then actively wait for an approval event after submitting a task:

```powershell
python -m bridge.cli event wait-approval --conversation-id <id> --tool-name run_command --timeout-seconds 300
python -m bridge.cli approval inspect --id <id>
python -m bridge.cli approval respond --id <id> --decision allow --option-name <exact-option-text> --button-name <exact-submit-button-name> --tool-name run_command --event-observed-at <observedAt> --dialog-fingerprint <fingerprint> --confirm-approval
```

Use `observedAt` from the matching event and the fingerprint from `approval inspect` at `detail.approval.fingerprint`. Option and button names must match the current dialog exactly. To deny, use `--decision deny` with the actual deny option. Inspect again if the dialog changes. An event cannot be responded to twice. The user may authorize a specific command or delegate approval decisions within a defined task scope.

Antigravity schedules independent of the Codex host have a minimum interval of 60 seconds:

```powershell
Get-Content -Raw -Encoding UTF8 .\prompt.txt | python -m bridge.cli schedule create --prompt-stdin --interval-seconds 3600 --confirm-schedule
python -m bridge.cli schedule list
python -m bridge.cli schedule remove --schedule-id <id> --confirm-schedule
```

Prefer the host's existing scheduler for recurring supervision. Sidecar schedules persist prompts so they can execute again. Stopping scheduling does not mean an already running task was canceled.

## Low-level page control and links

Prefer domain commands. For low-level control, inspect the actual DOM and choose a unique control first. Example selectors must match the current page:

```powershell
python -m bridge.cli control get --id <id> --selector "button[data-testid='send-button']"
python -m bridge.cli control wait --id <id> --selector "body" --state visible
python -m bridge.cli control click --id <id> --selector "button[data-testid='send-button']" --confirm-control
Get-Content -Raw -Encoding UTF8 .\input.txt | python -m bridge.cli control fill --id <id> --selector "[contenteditable='true']" --text-stdin --confirm-control
python -m bridge.cli control press --id <id> --selector "[contenteditable='true']" --key Enter --confirm-control
```

Use `--nth <zero-based-index>` only after confirming the ordering of multiple matches. See each command's `--help` for its complete options.

```powershell
python -m bridge.cli live --id <id>
python -m bridge.cli list
python -m bridge.cli remove --id <link-id>
python -m bridge.cli sync --source .\external-links.json
```

## Authorization and data locations

Discovery, reads, snapshots, listings, waits, and status checks are read-only. Writes for conversations, pages, models, projects, settings, artifacts, approvals, and schedules use their corresponding `--confirm-*` options after user authorization. For an unknown write outcome, inspect state and receipts before deciding the next action.

The plugin connects only to local loopback endpoints. It exposes no arbitrary JavaScript, arbitrary navigation, upload/download, or credential-reading commands and does not directly rewrite Antigravity's private conversation store. Hooks retain allowlisted conversation, tool-name, and approval-state fields rather than complete tool arguments.

| Data | Default location |
| --- | --- |
| Managed `agy` | `$CODEX_HOME/tools/agy` |
| Installer download cache | `$CODEX_HOME/tools/cache/agy-staging` |
| Links, tasks, events, and receipts | `$CODEX_HOME/plugins/data/codex-dynamic-bridge` |
| Companion files and global configuration | `$HOME/.gemini/config` |
| Companion events and schedules | `$HOME/.gemini/antigravity/sidecar_data/codex-dynamic-bridge/codex-bridge/data` |

When `CODEX_HOME` is unset, it defaults to `$HOME/.codex`. Schedule prompts remain in `schedules.json`; model response bodies are not written to that schedule file.

## Update and removal

Update the Codex side, then start a new conversation to load its skill:

```powershell
codex plugin marketplace upgrade codex-dynamic-bridge
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin add codex-dynamic-bridge@codex-dynamic-bridge
```

Check Companion and `agy` next and update both sides with the setup flow above. Before removing the Codex plugin, uninstall Companion while the plugin is still available:

```powershell
python -m bridge.cli companion uninstall-global --confirm-uninstall
codex plugin remove codex-dynamic-bridge@codex-dynamic-bridge
codex plugin marketplace remove codex-dynamic-bridge
```

Removal deletes only this Companion directory and its Sidecar entry. Links, events, receipts, and schedule data are retained by default. When `restartRequired: true` is returned, fully exit and restart Antigravity. The final state should show `installed: false`, `enabled: false`, and `endpointReady: false`. A retained `endpoint.json` does not mean the service is available. Before complete cleanup, verify the paths above and confirm that schedule data is no longer needed.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No `codex plugin` command | Check `codex plugin --help` and update to a plugin-capable Codex build |
| Marketplace download or name conflict | Check GitHub connectivity and `codex plugin marketplace list`, then inspect the old source before changing it |
| Missing DevTools port | Confirm Antigravity is running and `%APPDATA%\Antigravity\DevToolsActivePort` exists; restart to refresh a stale port |
| Missing Playwright | Print `sys.executable`, install into that same Python on the chosen drive, and verify the import |
| Client exists without a conversation | Use `discover-pages` and `conversation open-new` from the single trusted shell |
| Supplement remains queued | Inspect the `send-now` receipt and page; check an unknown result before resending |
| Missing approval events | Check Companion, Hook paths, and restart status, then run `event wait-approval` |
| Interrupted installation or busy files | Repeat `setup install`; `pending` retains verified cache data for retry |
| DOM changes break control | Reinspect the page and selectors with `control inspect`/`snapshot` |

Failed Windows downloads do not replace the existing `agy.exe`. Resume data is checked for length, response ranges, and SHA-512. Issue reports should include Codex, Python, and Antigravity versions, the command, and diagnostics with conversation text, tokens, and sensitive paths removed.

## Development

```powershell
git clone https://github.com/GuraQwQ/codex-dynamic-bridge.git
Set-Location .\codex-dynamic-bridge\plugins\codex-dynamic-bridge
python -B -m bridge.cli --help
python -B -m bridge.cli doctor
```

Bridge code is under `bridge/`, the Antigravity side under `companion/antigravity-plugin/`, and agent instructions in [SKILL.md](skills/codex-dynamic-bridge/SKILL.md). `doctor` checks local availability without writing. Real page sends, approvals, and official CLI behavior require their corresponding runtime environments.

MIT. See [LICENSE](LICENSE).
