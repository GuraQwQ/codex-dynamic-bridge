import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


VERSION = json.loads((Path(__file__).resolve().parents[2] / "plugin.json").read_text(encoding="utf-8"))["version"]
MAX_BODY_BYTES = 1_048_576
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
SCHEDULE_WORKERS = 4
EVENT_FIELDS = {
    "kind",
    "conversationId",
    "workspacePaths",
    "artifactDirectoryPath",
    "modelName",
    "terminationReason",
    "fullyIdle",
    "error",
    "stepIdx",
    "invocationNum",
    "initialNumSteps",
    "executionNum",
    "toolName",
    "projectId",
    "status",
    "approvalState",
    "observedAt",
    "source",
    "parentConversationId",
    "agentRole",
    "agentType",
    "agentState",
    "workspaceUris",
    "cliStatus",
    "stepState",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def data_dir():
    return Path(
        os.environ.get(
            "ANTIGRAVITY_EXECUTABLE_DATA_DIR",
            Path(os.environ.get("TEMP", Path.home())) / "codex-dynamic-bridge-sidecar",
        )
    )


DATA_DIR = data_dir()
EVENTS_PATH = DATA_DIR / "events.jsonl"
SCHEDULES_PATH = DATA_DIR / "schedules.json"
EVENT_LOCK = threading.Lock()
SCHEDULE_LOCK = threading.Lock()


def atomic_write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_agentapi(arguments, timeout=300):
    try:
        result = subprocess.run(
            ["agentapi", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"agentapi 执行失败: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip()
        raise RuntimeError(f"agentapi 退出码 {result.returncode}: {detail[:1000]}")
    raw = result.stdout.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"output": raw}
    if isinstance(parsed, dict) and not parsed.get("conversationId"):
        match = re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}", raw)
        if match:
            parsed["conversationId"] = match.group(0)
    return parsed


def validate_prompt(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("prompt 必须是非空字符串")
    return value


def validate_conversation_id(value, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError("conversation ID 必须为 1..128 位字母、数字或连字符，且以字母或数字开头")
    return value


def validate_event(event):
    if not isinstance(event, dict):
        raise ValueError("事件必须是 JSON 对象")
    validate_conversation_id(event.get("conversationId"))
    if not isinstance(event.get("kind"), str) or not event["kind"].strip():
        raise ValueError("事件 kind 必须是非空字符串")
    observed_at = event.get("observedAt")
    if not isinstance(observed_at, str):
        raise ValueError("事件 observedAt 必须是带时区的 ISO 8601 时间戳")
    try:
        timestamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("事件 observedAt 必须是带时区的 ISO 8601 时间戳") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("事件 observedAt 必须包含时区")


def append_event(payload):
    event = {key: payload[key] for key in EVENT_FIELDS if key in payload}
    tool_call = payload.get("toolCall")
    tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
    if isinstance(tool_name, str) and tool_name.strip():
        event["toolName"] = tool_name.strip()[:128]
    if event.get("kind") == "PreToolUse":
        event.setdefault("approvalState", "requested")
        event.setdefault("status", "waiting_approval")
    event.setdefault("observedAt", utc_now())
    validate_event(event)
    encoded = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    with EVENT_LOCK, EVENTS_PATH.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell():
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                # 保留中断留下的尾行，以分隔符隔离下一条完整事件。
                stream.write(b"\n")
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return event


def list_events(conversation_id=None, limit=100, after=None, stream_id=None):
    with EVENT_LOCK:
        try:
            stream = EVENTS_PATH.open("rb")
        except FileNotFoundError:
            return {"events": [], "nextCursor": 0, "hasMore": False,
                    "streamId": "empty", "reset": bool(after or stream_id not in (None, "empty")),
                    "corruptLines": [], "incompleteLine": None}
        with stream:
            return read_event_page(stream, conversation_id, limit, after, stream_id)


def read_event_page(stream, conversation_id, limit, after, stream_id):
    stat = os.fstat(stream.fileno())
    identity = f"{stat.st_dev}:{stat.st_ino}:{getattr(stat, 'st_birthtime_ns', 0)}"
    current_id = hashlib.sha256(identity.encode("ascii")).hexdigest()
    reset = stream_id is not None and stream_id != current_id
    start = 0 if reset else (after or 0)
    while True:
        events = deque(maxlen=limit)
        cursor = 0
        has_more = False
        corrupt_lines = []
        incomplete_line = None
        for line_number, line in enumerate(stream, start=1):
            # 正在追加的尾行尚未提交，留给下一次查询。
            if not line.endswith(b"\n"):
                incomplete_line = line_number
                break
            if line_number > start and line.strip():
                try:
                    event = json.loads(line.decode("utf-8"))
                    validate_event(event)
                except ValueError:
                    corrupt_lines.append(line_number)
                    event = None
                if event is not None and (not conversation_id or event.get("conversationId") == conversation_id):
                    if after is not None and len(events) == limit:
                        has_more = True
                        break
                    events.append(event)
            cursor = line_number
        if cursor < start:
            start = 0
            reset = True
            stream.seek(0)
            continue
        return {"events": list(events), "nextCursor": cursor, "hasMore": has_more,
                "streamId": current_id, "reset": reset,
                "corruptLines": corrupt_lines, "incompleteLine": incomplete_line}


def load_schedules():
    if not SCHEDULES_PATH.exists():
        return []
    value = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def save_schedules(schedules):
    atomic_write(SCHEDULES_PATH, schedules)


def execute_schedule(schedule):
    try:
        prompt = validate_prompt(schedule.get("prompt"))
        conversation_id = validate_conversation_id(schedule.get("conversationId"), optional=True)
        if conversation_id:
            result = run_agentapi(
                ["send-message", conversation_id, prompt]
            )
        else:
            result = run_agentapi(["new-conversation", prompt])
        schedule["lastStatus"] = "success"
        schedule.pop("lastError", None)
        conversation_id = result.get("conversationId") if isinstance(result, dict) else None
        if conversation_id:
            schedule["lastConversationId"] = conversation_id
    except Exception as exc:
        schedule["lastStatus"] = "error"
        schedule["lastError"] = str(exc)[:1000]
    schedule["lastRunAt"] = utc_now()
    schedule["nextRunEpoch"] = time.time() + schedule["intervalSeconds"]


def run_scheduled_task(schedule, running):
    try:
        execute_schedule(schedule)
        with SCHEDULE_LOCK:
            schedules = load_schedules()
            current = next((item for item in schedules if item.get("id") == schedule["id"]), None)
            if current:
                current.pop("lastError", None)
                current.update({key: value for key, value in schedule.items()
                                if key.startswith("last") or key == "nextRunEpoch"})
                save_schedules(schedules)
    finally:
        with SCHEDULE_LOCK:
            running.discard(schedule["id"])


def scheduler_loop(stop_event=None):
    stop_event = stop_event or threading.Event()
    running = set()
    with SCHEDULE_LOCK:
        schedules = load_schedules()
        recovered = False
        for schedule in schedules:
            if schedule.get("lastStatus") == "running":
                # 上次发送结果未知，按周期继续，避免重启后立即重复发送。
                schedule.update(lastStatus="interrupted", lastError="Sidecar 重启，上次执行结果未知",
                                nextRunEpoch=time.time() + schedule["intervalSeconds"])
                recovered = True
        if recovered:
            save_schedules(schedules)
    while not stop_event.is_set():
        with SCHEDULE_LOCK:
            schedules = load_schedules()
            due = []
            for schedule in schedules:
                if len(running) >= SCHEDULE_WORKERS:
                    break
                if (schedule.get("enabled", True) and schedule["id"] not in running
                        and schedule.get("nextRunEpoch", 0) <= time.time()):
                    running.add(schedule["id"])
                    schedule["lastStatus"] = "running"
                    due.append(dict(schedule))
            if due:
                save_schedules(schedules)
        for scheduled_run in due:
            threading.Thread(target=run_scheduled_task, args=(scheduled_run, running), daemon=True).start()
        stop_event.wait(1)


class Handler(BaseHTTPRequestHandler):
    server_version = f"CodexDynamicBridge/{VERSION}"

    def log_message(self, format_string, *args):
        return

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return hmac.compare_digest(
            self.headers.get("Authorization", "").encode("utf-8"),
            f"Bearer {self.server.token}".encode("utf-8"),
        )

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("无效 Content-Length") from exc
        if not 0 < length <= MAX_BODY_BYTES:
            raise ValueError("请求体为空或超过限制")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def do_GET(self):
        if not self.authorized():
            self.respond(401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            self.respond(200, {"status": "ok", "version": VERSION,
                               "capabilities": {"eventStreamCursor": True}})
            return
        if parsed.path == "/v1/events":
            query = parse_qs(parsed.query)
            conversation_id = query.get("conversation_id", [None])[0]
            try:
                limit = int(query.get("limit", [100])[0])
                raw_after = query.get("after", [None])[0]
                after = int(raw_after) if raw_after is not None else None
                if not 1 <= limit <= 1000 or (after is not None and after < 0):
                    raise ValueError
            except (TypeError, ValueError):
                self.respond(400, {"error": "limit 必须为 1..1000，after 必须为非负整数"})
                return
            self.respond(200, list_events(
                conversation_id, limit=limit, after=after,
                stream_id=query.get("stream_id", [None])[0],
            ))
            return
        if parsed.path == "/v1/schedules":
            with SCHEDULE_LOCK:
                schedules = load_schedules()
            self.respond(200, {"schedules": schedules})
            return
        self.respond(404, {"error": "not_found"})

    def do_POST(self):
        if not self.authorized():
            self.respond(401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            prompt = payload.get("prompt", "")
            if parsed.path == "/v1/conversations":
                validate_prompt(prompt)
                self.respond(200, run_agentapi(["new-conversation", prompt]))
                return
            match = re.fullmatch(r"/v1/conversations/([^/]+)/messages", parsed.path)
            if match:
                conversation_id = validate_conversation_id(match.group(1))
                validate_prompt(prompt)
                self.respond(
                    200,
                    run_agentapi(["send-message", conversation_id, prompt]),
                )
                return
            if parsed.path == "/v1/events":
                self.respond(200, append_event(payload))
                return
            if parsed.path == "/v1/schedules":
                interval = payload.get("intervalSeconds")
                if type(interval) is not int or interval < 60:
                    raise ValueError("intervalSeconds 必须为至少 60 的整数")
                validate_prompt(prompt)
                conversation_id = validate_conversation_id(payload.get("conversationId"), optional=True)
                schedule = {
                    "id": str(uuid.uuid4()),
                    "prompt": prompt,
                    "conversationId": conversation_id,
                    "intervalSeconds": interval,
                    "enabled": True,
                    "createdAt": utc_now(),
                    "nextRunEpoch": time.time() + interval,
                }
                with SCHEDULE_LOCK:
                    schedules = load_schedules()
                    schedules.append(schedule)
                    save_schedules(schedules)
                self.respond(201, schedule)
                return
            self.respond(404, {"error": "not_found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})
        except Exception as exc:
            self.respond(500, {"error": str(exc)[:1000]})

    def do_DELETE(self):
        if not self.authorized():
            self.respond(401, {"error": "unauthorized"})
            return
        match = re.fullmatch(r"/v1/schedules/([^/]+)", urlparse(self.path).path)
        if not match:
            self.respond(404, {"error": "not_found"})
            return
        schedule_id = match.group(1)
        with SCHEDULE_LOCK:
            schedules = load_schedules()
            remaining = [item for item in schedules if item.get("id") != schedule_id]
            if len(remaining) != len(schedules):
                save_schedules(remaining)
        if len(remaining) == len(schedules):
            self.respond(404, {"error": "schedule_not_found"})
            return
        self.respond(200, {"removed": schedule_id})


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.token = token
    endpoint_path = DATA_DIR / "endpoint.json"
    atomic_write(
        endpoint_path,
        {"url": f"http://127.0.0.1:{server.server_port}", "token": token},
    )
    try:
        os.chmod(endpoint_path, 0o600)
    except OSError:
        pass
    scheduler_stop = threading.Event()
    threading.Thread(target=scheduler_loop, args=(scheduler_stop,), daemon=True).start()
    try:
        server.serve_forever()
    finally:
        scheduler_stop.set()
        server.server_close()
        endpoint_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
