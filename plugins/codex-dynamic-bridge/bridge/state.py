import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse


EVENT_FIELDS = {
    "conversationId",
    "workspacePaths",
    "artifactDirectoryPath",
    "modelName",
    "terminationReason",
    "fullyIdle",
    "error",
    "stepIdx",
    "toolName",
    "projectId",
    "status",
    "approvalState",
    "source",
    "invocationNum",
    "initialNumSteps",
    "executionNum",
    "parentConversationId",
    "agentRole",
    "agentType",
    "agentState",
    "workspaceUris",
    "cliStatus",
    "stepState",
}
TASK_FIELDS = {
    "conversationId",
    "codexTaskId",
    "projectId",
    "title",
    "url",
    "model",
    "status",
    "artifactDirectoryPath",
    "updatedAt",
    "workspacePaths",
    "lastObservedAt",
    "submissionId",
    "lastSubmittedAt",
    "lastMetadataAt",
    "parentConversationId",
    "agentRole",
    "agentType",
    "agentState",
    "workspaceUris",
    "cliStatus",
}


class StateError(RuntimeError):
    """桥接状态文件无效或请求无法完成。"""


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def event_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise StateError("事件时间必须包含时区")
    return parsed


def after_event(event, after):
    return not after or event_time(event["observedAt"]) > event_time(after)


def completed_event(events, after=None):
    latest = max(
        (event for event in reversed(events) if after_event(event, after)
         and event.get("kind") != "Subagent"
         and not (event.get("kind") == "CliResult" and event.get("cliStatus") == "SUCCESS")),
        key=lambda event: event_time(event["observedAt"]), default=None,
    )
    if latest and latest.get("kind") == "Stop" and latest.get("fullyIdle") is True:
        return latest
    return None


def execution_state(events):
    observed = [event for event in events if event.get("kind") != "Subagent"]
    if not observed:
        return "unobserved"
    if completed_event(observed):
        return "stopped"
    latest = max(reversed(observed), key=lambda event: event_time(event["observedAt"]))
    if latest.get("kind") == "CliResult":
        return {"SUCCESS": "turn_completed", "ERROR": "failed", "INVALID": "failed",
                "CANCELED": "canceled", "INTERRUPTED": "canceled",
                "WAITING": "waiting_input", "RUNNING": "running"}.get(
                    latest.get("cliStatus"), "unobserved")
    return "waiting_approval" if latest.get("approvalState") == "requested" else "running"


def native_stream_events(message, conversation_id):
    """只提取 CLI 状态与归属，不保存正文、工具参数或子 Agent 日志。"""
    kind = message.get("event")
    payload = message.get({"init": "init", "step_update": "step_update", "result": "result"}.get(kind))
    if not isinstance(payload, dict):
        return []
    current_id = message.get("conversation_id") if kind == "init" else payload.get("conversation_id")
    current_id = current_id or conversation_id
    if not isinstance(current_id, str) or not current_id:
        raise StateError("CLI 流式事件缺少 conversation ID")
    event = {"kind": {"init": "CliInit", "step_update": "CliStep", "result": "CliResult"}[kind],
             "conversationId": current_id, "source": "agy", "observedAt": utc_now()}
    if kind == "init":
        if isinstance(payload.get("cwd"), str) and payload["cwd"]:
            event["workspacePaths"] = [payload["cwd"]]
        if isinstance(payload.get("model"), str):
            event["modelName"] = payload["model"]
    elif kind == "step_update":
        if isinstance(payload.get("step_index"), int):
            event["stepIdx"] = payload["step_index"]
        if isinstance(payload.get("state"), str):
            event["stepState"] = payload["state"]
        if isinstance(payload.get("tool_name"), str):
            event["toolName"] = payload["tool_name"][:128]
        error = payload.get("tool_info", {}).get("error") if isinstance(payload.get("tool_info"), dict) else None
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            event["error"] = error["message"][:1024]
    else:
        event["cliStatus"] = payload.get("status")
        if isinstance(payload.get("error"), str):
            event["error"] = payload["error"][:1024]
    result = [event]
    details = payload.get("subagent_info")
    children = details.get("subagents", []) if isinstance(details, dict) else []
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, dict) or not isinstance(child.get("conversation_id"), str):
            continue
        child_id = child["conversation_id"]
        if not child_id or child_id == current_id:
            continue
        record = {"kind": "Subagent", "conversationId": child_id,
                  "parentConversationId": current_id, "source": "agy",
                  "observedAt": event["observedAt"]}
        for original, target in (("role", "agentRole"), ("type_name", "agentType"), ("state", "agentState")):
            if isinstance(child.get(original), str):
                record[target] = child[original][:128]
        uris = child.get("workspace_uris")
        if isinstance(uris, list):
            record["workspaceUris"] = [uri for uri in uris if isinstance(uri, str)]
            paths = []
            for uri in record["workspaceUris"]:
                parsed = urlparse(uri)
                if parsed.scheme != "file":
                    continue
                path = unquote(parsed.path)
                if parsed.netloc and parsed.netloc != "localhost":
                    path = "//" + parsed.netloc + path
                elif len(path) >= 3 and path[0] == "/" and path[2] == ":":
                    path = path[1:]
                paths.append(path)
            if paths:
                record["workspacePaths"] = paths
        result.append(record)
    return result


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 锁定独立 inode，目标文件可在短事务中原子替换；进程退出时操作系统释放锁。
    with path.with_name(path.name + ".lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def default_data_dir(env=None):
    env = os.environ if env is None else env
    override = env.get("CODEX_DYNAMIC_BRIDGE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    codex_home = Path(env.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "plugins" / "data" / "codex-dynamic-bridge"


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


class EventStore:
    def __init__(self, path=None):
        self.path = Path(path or default_data_dir() / "events.jsonl")

    def append(self, kind, payload):
        if not isinstance(payload, dict):
            raise StateError("Hook 输入必须是 JSON 对象")
        tool_call = payload.get("toolCall")
        tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
        event = {
            "kind": kind,
            "observedAt": utc_now(),
            **{key: payload[key] for key in EVENT_FIELDS if key in payload},
        }
        if isinstance(tool_name, str) and tool_name.strip():
            event["toolName"] = tool_name.strip()[:128]
        if kind == "PreToolUse":
            event.setdefault("approvalState", "requested")
            event.setdefault("status", "waiting_approval")
        if not event.get("conversationId"):
            raise StateError("Hook 输入缺少 conversationId")
        self.import_events([event])
        return event

    def list(self, conversation_id=None, limit=100):
        if not self.path.exists():
            return []
        events = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StateError(f"事件文件第 {line_number} 行不是有效 JSON") from exc
                if not conversation_id or event.get("conversationId") == conversation_id:
                    events.append(event)
        events.sort(key=lambda event: event_time(event["observedAt"]))
        return events if limit is None else (events[-limit:] if limit else [])

    def latest(self, conversation_id, kind=None):
        events = self.list(conversation_id=conversation_id, limit=1000)
        if kind:
            events = [event for event in events if event.get("kind") == kind]
        return events[-1] if events else None

    def import_events(self, events):
        with file_lock(self.path):
            return self._import_events(events)

    def _import_events(self, events):
        records = self.list(limit=None)
        existing = {
            json.dumps(event, ensure_ascii=False, sort_keys=True)
            for event in records
        }
        imported = []
        for event in events:
            if not isinstance(event, dict):
                continue
            normalized = {
                "kind": event.get("kind", "Unknown"),
                "observedAt": event.get("observedAt", utc_now()),
                **{key: event[key] for key in EVENT_FIELDS if key in event},
            }
            serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if serialized in existing or not normalized.get("conversationId"):
                continue
            existing.add(serialized)
            imported.append(normalized)
        if imported:
            temporary_path = None
            try:
                # ponytail: 每页重写本地日志以保证中断原子性；日志显著增长后再迁移 SQLite。
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.path.parent, delete=False
                ) as stream:
                    temporary_path = Path(stream.name)
                    for event in records + imported:
                        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, self.path)
            finally:
                if temporary_path and temporary_path.exists():
                    temporary_path.unlink()
        return imported

    def wait(self, conversation_id, timeout_seconds=30, poll_seconds=0.25, after=None):
        deadline = time.monotonic() + timeout_seconds
        while True:
            event = completed_event(self.list(conversation_id=conversation_id, limit=1000), after)
            if event:
                return event
            if time.monotonic() >= deadline:
                raise StateError(f"等待会话完成超时: {conversation_id}")
            time.sleep(poll_seconds)

    def wait_approval(
        self,
        conversation_id,
        timeout_seconds=30,
        poll_seconds=0.25,
        tool_name=None,
        after=None,
    ):
        deadline = time.monotonic() + timeout_seconds
        while True:
            events = self.list(conversation_id=conversation_id, limit=1000)
            for event in reversed(events):
                if event.get("kind") != "PreToolUse":
                    continue
                if event.get("approvalState") != "requested":
                    continue
                if tool_name and event.get("toolName") != tool_name:
                    continue
                if not after_event(event, after):
                    continue
                return event
            if time.monotonic() >= deadline:
                raise StateError(f"等待会话审批请求超时: {conversation_id}")
            time.sleep(poll_seconds)

    def summary(self, conversation_id):
        all_events = self.list(limit=None)
        grouped = {}
        parents = {}
        for event in all_events:
            current_id = event["conversationId"]
            grouped.setdefault(current_id, []).append(event)
            if event.get("parentConversationId"):
                parents[current_id] = event["parentConversationId"]
        events = grouped.get(conversation_id, [])
        if not events:
            raise StateError(f"未找到会话事件: {conversation_id}")

        def describe(current_id):
            history = grouped[current_id]
            metadata = {}
            for name in ("workspacePaths", "workspaceUris", "parentConversationId", "agentRole", "agentType", "agentState"):
                metadata[name] = next((event[name] for event in reversed(history) if name in event), None)
            return {"conversationId": current_id, **metadata,
                    "execution": execution_state(history), "lastObservedAt": history[-1]["observedAt"]}

        descendants = []
        pending = [conversation_id]
        seen = {conversation_id}
        children = {}
        for child, parent in parents.items():
            children.setdefault(parent, []).append(child)
        while pending:
            current_id = pending.pop()
            for child in children.get(current_id, []):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
                    descendants.append(describe(child))
        tool_counts = {}
        errors = []
        for event in events:
            tool = event.get("toolName")
            if tool:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            if event.get("error"):
                errors.append(event["error"])
        latest = events[-1]
        stop = next(
            (event for event in reversed(events) if event.get("kind") == "Stop"),
            None,
        )
        return {
            **describe(conversation_id),
            "status": "idle" if completed_event(events) else execution_state(events),
            "eventCount": len(events),
            "model": next((event["modelName"] for event in reversed(events) if event.get("modelName")), None),
            "projectId": next((event["projectId"] for event in reversed(events) if event.get("projectId")), None),
            "artifactDirectoryPath": next((event["artifactDirectoryPath"] for event in reversed(events) if event.get("artifactDirectoryPath")), None),
            "lastObservedAt": latest.get("observedAt"),
            "terminationReason": stop.get("terminationReason") if stop else None,
            "toolCounts": dict(sorted(tool_counts.items())),
            "subagentEventCount": sum(
                tool_counts.get(name, 0)
                for name in ("invoke_subagent", "define_subagent", "manage_subagents", "send_message")
            ),
            "errors": errors[-10:],
            "agents": sorted(descendants, key=lambda item: item["conversationId"]),
        }


class TaskStore:
    def __init__(self, path=None):
        self.path = Path(path or default_data_dir() / "tasks.json")

    def load(self):
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError("任务映射文件不是有效 JSON") from exc
        if not isinstance(value, list):
            raise StateError("任务映射文件根节点必须是数组")
        return value

    def upsert(self, record):
        return self.upsert_many([record])[0]

    def upsert_many(self, records):
        with file_lock(self.path):
            tasks = {task["conversationId"]: task for task in self.load()}
            updated = [self._merge(tasks, record) for record in records]
            if updated:
                ordered = sorted(tasks.values(), key=lambda task: task.get("updatedAt", ""), reverse=True)
                atomic_write_json(self.path, ordered)
            return updated

    def _merge(self, tasks, record):
        if not isinstance(record, dict) or not record.get("conversationId"):
            raise StateError("任务映射必须包含 conversationId")
        normalized = {
            key: record[key]
            for key in TASK_FIELDS
            if key in record and record[key] not in (None, "")
        }
        normalized["updatedAt"] = utc_now()
        old = tasks.get(normalized["conversationId"], {})
        if normalized.get("lastMetadataAt") and old.get("lastMetadataAt"):
            if event_time(normalized["lastMetadataAt"]) < event_time(old["lastMetadataAt"]):
                return old
        if normalized.get("lastSubmittedAt") and old.get("lastSubmittedAt"):
            if event_time(normalized["lastSubmittedAt"]) < event_time(old["lastSubmittedAt"]):
                return old
        if normalized.get("lastObservedAt"):
            previous = [old[key] for key in ("lastObservedAt", "lastSubmittedAt") if old.get(key)]
            if previous and event_time(normalized["lastObservedAt"]) < max(map(event_time, previous)):
                return old
        if normalized.get("lastSubmittedAt") and old.get("lastObservedAt"):
            if event_time(old["lastObservedAt"]) >= event_time(normalized["lastSubmittedAt"]):
                normalized.pop("status", None)
        if normalized.get("cliStatus") == "SUCCESS" and old.get("status") == "idle":
            normalized.pop("status", None)
        merged = {**old, **normalized}
        tasks[normalized["conversationId"]] = merged
        return merged

    def remove(self, conversation_id):
        with file_lock(self.path):
            return self._remove(conversation_id)

    def _remove(self, conversation_id):
        tasks = self.load()
        remaining = [item for item in tasks if item.get("conversationId") != conversation_id]
        if len(remaining) == len(tasks):
            raise StateError(f"未找到任务映射: {conversation_id}")
        atomic_write_json(self.path, remaining)
        return {"removed": conversation_id, "total": len(remaining)}

    def sync_event(self, event):
        return self.sync_events([event])[0]

    def sync_events(self, events):
        records = [{
            "conversationId": event["conversationId"],
            "model": event.get("modelName"),
            "status": (None if event.get("kind") == "Subagent" else
                       "idle" if event.get("fullyIdle") else event.get("status") or execution_state([event])),
            "artifactDirectoryPath": event.get("artifactDirectoryPath"),
            "projectId": event.get("projectId"),
            "workspacePaths": event.get("workspacePaths"),
            "lastObservedAt": event.get("observedAt") if event.get("kind") != "Subagent" else None,
            "lastMetadataAt": event.get("observedAt") if event.get("kind") == "Subagent" else None,
            **{key: event[key] for key in ("parentConversationId", "agentRole", "agentType", "agentState", "workspaceUris", "cliStatus") if key in event},
        } for event in sorted(events, key=lambda item: event_time(item["observedAt"]))]
        return self.upsert_many(records)


def artifact_root_for_conversation(event_store, conversation_id):
    events = event_store.list(conversation_id=conversation_id, limit=1000)
    for event in reversed(events):
        root = event.get("artifactDirectoryPath")
        if root:
            path = Path(root).expanduser().resolve()
            if path.is_dir():
                return path
    raise StateError(f"没有可用的产物目录: {conversation_id}")


def list_artifacts(root, limit=200):
    root = Path(root).resolve()
    blocked = {"token", "credential", "cookie", "secret", "key"}
    items = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not path.is_file():
            continue
        lowered = path.name.lower()
        if any(word in lowered for word in blocked):
            continue
        items.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
            }
        )
        if len(items) >= limit:
            break
    return items


def read_artifact(root, relative_path, max_bytes=1_048_576):
    root = Path(root).resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise StateError("产物路径越界") from exc
    if target.suffix.lower() not in {".md", ".txt", ".json", ".diff", ".patch", ".log"}:
        raise StateError("只允许读取文本产物")
    if not target.is_file():
        raise StateError(f"产物不存在: {relative_path}")
    if target.stat().st_size > max_bytes:
        raise StateError(f"产物超过读取上限 {max_bytes} 字节")
    return target.read_text(encoding="utf-8")
