import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


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
}


class StateError(RuntimeError):
    """桥接状态文件无效或请求无法完成。"""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
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
        return events[-limit:]

    def latest(self, conversation_id, kind=None):
        events = self.list(conversation_id=conversation_id, limit=1000)
        if kind:
            events = [event for event in events if event.get("kind") == kind]
        return events[-1] if events else None

    def import_events(self, events):
        existing = {
            json.dumps(event, ensure_ascii=False, sort_keys=True)
            for event in self.list(limit=1_000_000)
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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(normalized, ensure_ascii=False) + "\n")
            existing.add(serialized)
            imported.append(normalized)
        return imported

    def wait(self, conversation_id, timeout_seconds=30, poll_seconds=0.25, after=None):
        deadline = time.monotonic() + timeout_seconds
        while True:
            for event in reversed(self.list(conversation_id=conversation_id, limit=1000)):
                if event.get("kind") != "Stop" or event.get("fullyIdle") is not True:
                    continue
                if after and event.get("observedAt", "") <= after:
                    continue
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
                if after and event.get("observedAt", "") <= after:
                    continue
                return event
            if time.monotonic() >= deadline:
                raise StateError(f"等待会话审批请求超时: {conversation_id}")
            time.sleep(poll_seconds)

    def summary(self, conversation_id):
        events = self.list(conversation_id=conversation_id, limit=10_000)
        if not events:
            raise StateError(f"未找到会话事件: {conversation_id}")
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
            "conversationId": conversation_id,
            "status": "idle" if stop and stop.get("fullyIdle") else "running",
            "eventCount": len(events),
            "model": latest.get("modelName"),
            "projectId": latest.get("projectId"),
            "artifactDirectoryPath": latest.get("artifactDirectoryPath"),
            "lastObservedAt": latest.get("observedAt"),
            "terminationReason": stop.get("terminationReason") if stop else None,
            "toolCounts": dict(sorted(tool_counts.items())),
            "subagentEventCount": sum(
                tool_counts.get(name, 0)
                for name in ("invoke_subagent", "define_subagent", "manage_subagents", "send_message")
            ),
            "errors": errors[-10:],
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
        if not isinstance(record, dict) or not record.get("conversationId"):
            raise StateError("任务映射必须包含 conversationId")
        normalized = {
            key: record[key]
            for key in TASK_FIELDS
            if key in record and record[key] not in (None, "")
        }
        normalized["updatedAt"] = utc_now()
        tasks = self.load()
        old = next(
            (item for item in tasks if item.get("conversationId") == normalized["conversationId"]),
            {},
        )
        merged = {**old, **normalized}
        tasks = [
            item for item in tasks if item.get("conversationId") != normalized["conversationId"]
        ]
        tasks.append(merged)
        tasks.sort(key=lambda item: item.get("updatedAt", ""), reverse=True)
        atomic_write_json(self.path, tasks)
        return merged

    def remove(self, conversation_id):
        tasks = self.load()
        remaining = [item for item in tasks if item.get("conversationId") != conversation_id]
        if len(remaining) == len(tasks):
            raise StateError(f"未找到任务映射: {conversation_id}")
        atomic_write_json(self.path, remaining)
        return {"removed": conversation_id, "total": len(remaining)}

    def sync_event(self, event):
        record = {
            "conversationId": event["conversationId"],
            "model": event.get("modelName"),
            "status": "idle" if event.get("fullyIdle") else event.get("status", "running"),
            "artifactDirectoryPath": event.get("artifactDirectoryPath"),
            "projectId": event.get("projectId"),
        }
        return self.upsert(record)


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
