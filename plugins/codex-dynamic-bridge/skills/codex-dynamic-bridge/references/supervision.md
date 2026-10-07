# 监工生命周期

## 三个独立状态

每次插件投递生成一个回执，不保存提示词或回复正文。状态保存在插件数据目录的 `submissions/`，沿用 `CODEX_DYNAMIC_BRIDGE_DATA_DIR`，不放在源码缓存内。

| 字段 | 含义 | 不能推断的事实 |
| --- | --- | --- |
| `submission.delivery = accepted` | 后端调用返回，消息投递已被观察到接受 | 任务已完成、测试已通过 |
| `submission.delivery = dispatching/outcome_unknown` | 请求处理中、进程中断或结果不确定 | 可以安全重发 |
| `execution = stopped` | 本次观察窗口内最新事件为 `Stop/fullyIdle` | 满足验收要求 |
| `execution = turn_completed` | 原生 CLI 已返回本回合 `SUCCESS/result` | 后台任务完全空闲、测试通过 |
| `execution = failed/canceled/waiting_input` | 原生 CLI 报告失败、取消或等待输入 | 可以自动重发 |
| `execution = unobserved` | 尚无可归属的新事件 | 任务没执行、已失败 |
| `review.verdict = passed/failed` | 监工显式记录了结论和证据文件摘要 | 插件已替监工运行测试 |
| `review.verdict = unverified/stale` | 尚未验收，或证据文件已更改/丢失 | 可以当作验收通过 |

回执的 `submissionId` 是本地投递标识，不是 Antigravity 的原生 turn ID。Hook 暂未提供可直接关联的 turn ID，因此 `eventAttribution = after_submission` 只表示时间窗口关联。检查历史回执时，上界是同会话的下一次插件投递；不要把别的投递事件当作当前任务证据。用户或其它 Agent 同时操作同一会话时，必须结合当前界面和文件变更核对归属，不能仅凭时间戳作强保证。

## 持续监工

1. 确认项目、会话、工作区和用户授权的操作范围。是否自主审批由用户授权决定，不因使用 Sol 或 Astra 改变。
2. 通过现有命令投递后保留返回的 `submissionId`。若发送失败，转入核对，不自动更换后端重发。
3. Companion 可用且支持事件游标时，运行 `event sync`，随后用 `task inspect --conversation-id <id>` 读取增量状态。同步不会自动唤醒 Codex。
4. Hook 来源可用时，用 `conversation wait --conversation-id <id> --timeout-seconds 30` 等待新完成事件；已跟踪投递自动带时间下界。流式 CLI 没有 Hook 来源时，沿用宿主执行句柄读取最终结果，并查询本地任务状态。需要审批事件时使用 `event wait-approval`，传本次回执的 `submittedAt` 作为 `--after`。
5. 出现审批时先读取当前命令，再在授权范围内判断并响应；出现停止时按主技能的 Review diff / Git 变更优先流程验收。

Sol 或没有异步工具的宿主：使用有限时长的读取和等待，超时后检查状态并继续；不要把只读等待超时当作投递失败。返回了执行 session ID 时使用宿主提供的等待工具，不新建一个发送请求。没有 Companion/本地 Hook 事件时改为 CDP 页面与文件变更检查；可读取审批框，但完整的事件绑定自动审批仍需 Companion，不能假装空事件源可以返回结果。

Astra 且宿主确实提供异步工具：等待目标任务时可以处理独立检查；继续使用同一个回执和等待句柄接收结果。不要把 API 文档中的 `async: true` 填进本插件 CLI，也不要声称本插件自动开放了宿主未提供的工具。

原生电脑控制用于视觉检查和必要的界面操作，不替代已可用的结构化回执。经原生界面直接投递而未生成回执时，先记录发送前 UTC 时间，再在等待中显式提供 `--after`；这类任务不能声称已有自动投递跟踪。

## 原生 CLI 流式任务与子 Agent

```powershell
python -m bridge.cli conversation new --backend agy --stream --project-id <project-id> --prompt-stdin --confirm-create
python -m bridge.cli task submissions
python -m bridge.cli task inspect --conversation-id <id>
python -m bridge.cli activity --conversation-id <id>
```

这是单次任务的原生 NDJSON 读取，Bridge 的标准输出仍为最终 JSON。`init` 到达时即保存真实会话 ID；状态变化实时写入本地账本，可以在宿主执行工具返回 session ID 后边等待边做上述只读检查。文本分块不会反复记录相同状态，正文、工具参数和子 Agent 日志不进入账本。取消或超时保留已绑定回执，按恢复流程核对，不自动重发。

`activity.agents` 返回已观察到的父子会话、角色、实际工作区和执行状态，包括已知的嵌套子 Agent。`task list` 也保存父会话和工作区关联。以每个子会话的 `workspacePaths` 检查对应 worktree 的 Git diff 和测试；未知状态保持 `unobserved`，父回合返回不替代子 Agent 的停止证据。`workspaceUris` 保留原生 URI，非文件 URI 不自动换算为本机路径；WSL 或远程目录应在其真实执行环境中检查。

处理子 Agent 审批前，先用 `discover` 和页面检查确认该子会话的可寻址桌面目标，再绑定它自己的 `PreToolUse` 事件。只有父页面或审批框归属不明时，先定位子会话及当前审批对象，再执行对应审批工作流。

原生 `result` 只表示前台回合已返回；没有 `Stop/fullyIdle` 时状态为 `turn_completed`，`conversation wait` 继续等待完全空闲事件。已有空闲 Stop 不会被随后到达的成功结果或关系元数据遮蔽。`PreInvocation` 会在新模型调用开始时刷新状态，官方调用计数用于排查，不当作原生 turn ID。

读取结果中的 `diagnostics`。Headless 权限要求可能使工具未执行，而 CLI 仍返回 `SUCCESS`；记录实际测试证据后才可通过验收。流式 stdin 不提供审批响应通道，需要中途自主审批时仍使用已授权的桌面 Hook／审批工作流。

## 恢复中断

```powershell
python -m bridge.cli task submissions
python -m bridge.cli task inspect --submission-id <submission-id>
```

`dispatching` 可能是仍在进行的调用，也可能是进程被终止后留下的记录。先检查目标会话及工作区。即使本地有回执，也不能据此承诺远端严格一次执行。

如果新建会话已经成功，但返回 ID 前连接断开，先通过 `discover-pages` 与界面检查确定唯一真实会话，再补全关联：

```powershell
python -m bridge.cli task link --conversation-id <id> --submission-id <submission-id>
```

该命令只补元数据，不发送消息，也不把不确定投递自动改成已接受。已有回执不能改绑到其它会话。恢复后重新同步事件、检查文件和验收证据，再决定是否需要继续任务。

## 验收记录

由监工使用宿主现有 shell、测试工具或视觉工具执行实际验收。保存与当前变更对应的测试报告或评审文件，在用户授予的监工权限范围内记录结论：

```powershell
python -m bridge.cli task record-review --submission-id <submission-id> --verdict passed --evidence <absolute-report-path> --confirm-review
python -m bridge.cli task inspect --submission-id <submission-id>
```

不通过时用 `--verdict failed`。插件仅保存结论、报告路径、SHA-256 和记录时间，不运行报告内命令、不把报告全文存入账本。证据摘要只证明报告文件未变化，不证明代码未变化；继续产生代码变更后需要重新验收。同一投递的最新显式验收记录替换旧记录；新的投递不会继承旧投递的通过结论。

## 宿主与数据边界

周期监工优先调用当前宿主已提供的计划任务能力，并把会话 ID、项目路径、时区及验收要求放入任务说明。创建前只读检查宿主和 Sidecar 已有任务，区分“执行开发”与“检查验收”的职责，避免相同范围和时段重复安排。只有明确需要独立于宿主运行的 Antigravity 周期任务时才使用现有 Sidecar `schedule`；不擅自迁移或删除已有计划任务。

升级不改写 Sidecar 旧事件日志，不改动原有计划任务。首次同步从头读取旧日志，并沿用字段去重；之后在 `sync/` 中保存按端点文件与过滤范围区分的进度。每页事件、任务映射落盘后才推进游标，中断后重复执行 `event sync` 即可修复未完成页。日志应保持追加式；替换文件和截短会触发重读，不支持在同一文件内任意改写历史行后仍复用旧游标。
