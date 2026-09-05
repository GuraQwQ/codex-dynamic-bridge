import json
import os
import tempfile
import time
from contextlib import contextmanager
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
    "workspacePaths",
    "lastObservedAt",
    "submissionId",
    "lastSubmittedAt",
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
        (event for event in reversed(events) if after_event(event, after)),
        key=lambda event: event_time(event["observedAt"]), default=None,
    )
    if latest and latest.get("kind") == "Stop" and latest.get("fullyIdle") is True:
        return latest
    return None


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
            "status": "idle" if completed_event(events) else "running",
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
            "status": "idle" if event.get("fullyIdle") else event.get("status", "running"),
            "artifactDirectoryPath": event.get("artifactDirectoryPath"),
            "projectId": event.get("projectId"),
            "workspacePaths": event.get("workspacePaths"),
            "lastObservedAt": event.get("observedAt"),
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
