import codecs
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bridge.state import after_event, completed_event


class RuntimeBridgeError(RuntimeError):
    """外部 Antigravity 运行时不可用或返回无效结果。"""


def find_agy(env=None):
    uses_process_environment = env is None
    env = os.environ if env is None else env
    override = env.get("CODEX_DYNAMIC_BRIDGE_AGY")
    codex_home_value = env.get("CODEX_HOME")
    codex_home = Path(
        codex_home_value
        or (Path.home() / ".codex" if uses_process_environment else "__no_codex_home__")
    )
    plugin_root = Path(__file__).resolve().parents[1]
    bundled_name = "agy.exe" if os.name == "nt" else "agy"
    candidates = [
        override,
        shutil.which("agy"),
        str(codex_home / "tools" / "agy" / bundled_name),
        str(Path(env.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"),
    ]
    if uses_process_environment and not codex_home_value:
        candidates.append(str(plugin_root / "tools" / "agy" / bundled_name))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None


class AgyClient:
    def __init__(self, executable=None, runner=None, popen=None):
        self.executable = executable or find_agy()
        self.runner = runner or subprocess.run
        self.popen = popen or subprocess.Popen
        if not self.executable:
            raise RuntimeBridgeError(
                "未找到 Antigravity CLI；设置 CODEX_DYNAMIC_BRIDGE_AGY 或安装 agy"
            )

    def invoke(self, arguments, timeout_seconds=300, cwd=None, return_process=False):
        command = [self.executable, *arguments]
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                cwd=str(cwd) if cwd else None,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeBridgeError(f"无法执行 Antigravity CLI: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "未知错误").strip()
            raise RuntimeBridgeError(
                f"Antigravity CLI 退出码 {result.returncode}: {detail[:1000]}"
            )
        return result if return_process else result.stdout

    def capabilities(self):
        flags = dict.fromkeys(
            ("project", "conversation", "new-project", "model", "effort", "agent",
             "stream-json", "json-schema"),
            False,
        )
        result = {"available": False, "path": self.executable, "version": None, "flags": flags}
        try:
            version_process = self.invoke(["--version"], timeout_seconds=10, return_process=True)
            raw_version = (version_process.stdout or "") + "\n" + (version_process.stderr or "")
            match = re.search(r"\b(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\b", raw_version)
            if not match:
                raise RuntimeBridgeError("Antigravity CLI 未返回可识别版本")
            result["version"] = match.group(1)
            help_process = self.invoke(["--help"], timeout_seconds=10, return_process=True)
            help_text = (help_process.stdout or "") + "\n" + (help_process.stderr or "")
            for flag in flags:
                pattern = r"\bstream-json\b" if flag == "stream-json" else rf"(?<![\w-])--{flag}(?![\w-])"
                flags[flag] = bool(re.search(pattern, help_text))
            result["available"] = True
        except RuntimeBridgeError as exc:
            result["probeError"] = str(exc)
        return result

    def run_prompt(
        self,
        prompt,
        conversation_id=None,
        project_id=None,
        model=None,
        effort=None,
        agent=None,
        timeout_seconds=300,
        project_path=None,
        new_project=False,
        stream=False,
        on_event=None,
    ):
        arguments = ["-p", prompt, "--output-format", "stream-json" if stream else "json"]
        if conversation_id:
            arguments.append(f"--conversation={conversation_id}")
        if project_id:
            arguments.append(f"--project={project_id}")
        if new_project:
            arguments.append("--new-project")
        if model:
            arguments.extend(["--model", model])
        if effort:
            arguments.extend(["--effort", effort])
        if agent:
            arguments.extend(["--agent", agent])
        arguments.extend(["--print-timeout", f"{timeout_seconds}s"])
        cwd = None
        if project_path:
            cwd = Path(project_path).expanduser().resolve()
            if not cwd.is_dir():
                raise RuntimeBridgeError(f"项目目录不存在或不是目录: {cwd}")
        if stream:
            result, diagnostics = self._stream_prompt(arguments, timeout_seconds, cwd, on_event)
        else:
            process = self.invoke(
                arguments, timeout_seconds=timeout_seconds + 10, cwd=cwd, return_process=True
            )
            diagnostics = process.stderr or ""
            try:
                result = json.loads(process.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeBridgeError("Antigravity CLI 未返回有效 JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeBridgeError("Antigravity CLI JSON 根节点必须是对象")
        if result.get("status") != "SUCCESS":
            detail = result.get("error") or "Antigravity 任务未成功完成"
            if diagnostics.strip():
                detail = f"{detail}\nCLI 诊断: {diagnostics.strip()[-8192:]}"
            raise RuntimeBridgeError(detail)
        if diagnostics.strip():
            result["diagnostics"] = diagnostics.strip()[-8192:]
        return result

    def _stream_prompt(self, arguments, timeout_seconds, cwd, on_event):
        deadline = time.monotonic() + timeout_seconds
        try:
            process = self.popen(
                [self.executable, *arguments],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=str(cwd) if cwd else None,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeBridgeError(f"无法执行 Antigravity CLI: {exc}") from exc
        messages = queue.Queue(maxsize=128)
        stopped = threading.Event()
        diagnostics = [""]

        def enqueue(kind, value):
            while not stopped.is_set():
                try:
                    messages.put((kind, value), timeout=0.1)
                    return
                except queue.Full:
                    continue

        def read_stdout():
            try:
                for line in process.stdout:
                    if stopped.is_set():
                        return
                    enqueue("line", line)
            except (OSError, ValueError) as exc:
                enqueue("error", exc)
            finally:
                enqueue("end", None)

        def read_stderr():
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                while True:
                    # 按可用字节读取，短诊断无需等待缓冲区填满；增量解码保留跨块中文。
                    chunk = process.stderr.buffer.read1(4096)
                    if not chunk:
                        diagnostics[0] = (diagnostics[0] + decoder.decode(b"", final=True))[-8192:]
                        break
                    diagnostics[0] = (diagnostics[0] + decoder.decode(chunk))[-8192:]
            except (OSError, ValueError) as exc:
                enqueue("error", exc)

        readers = [threading.Thread(target=read_stdout, daemon=True),
                   threading.Thread(target=read_stderr, daemon=True)]
        result = None
        initialized = False
        failure = None
        try:
            for reader in readers:
                reader.start()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeBridgeError("Antigravity CLI 流式执行超时")
                try:
                    kind, value = messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise RuntimeBridgeError("Antigravity CLI 流式执行超时") from exc
                if kind == "end":
                    break
                if kind == "error":
                    raise RuntimeBridgeError(f"无法读取 Antigravity CLI 流式输出: {value}")
                if not value.strip():
                    continue
                try:
                    event = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise RuntimeBridgeError("Antigravity CLI 流式输出包含无效 JSON") from exc
                if not isinstance(event, dict):
                    raise RuntimeBridgeError("Antigravity CLI 流式事件必须是对象")
                event_type = event.get("event")
                if event_type not in ("init", "step_update", "result"):
                    raise RuntimeBridgeError(f"Antigravity CLI 流式事件类型无效: {event_type}")
                if not isinstance(event.get(event_type), dict):
                    raise RuntimeBridgeError(f"Antigravity CLI {event_type} 事件载荷无效")
                if result is not None or (event_type != "init" and not initialized):
                    raise RuntimeBridgeError("Antigravity CLI 流式事件顺序无效")
                if event_type == "init":
                    if initialized or not isinstance(event.get("conversation_id"), str) or not event["conversation_id"]:
                        raise RuntimeBridgeError("Antigravity CLI init 事件缺少会话 ID 或重复出现")
                    initialized = True
                if event_type == "result":
                    result = event["result"]
                if on_event:
                    try:
                        on_event(event)
                    except Exception as exc:
                        raise RuntimeBridgeError(f"Antigravity CLI 流式事件回调失败: {exc}") from exc
            try:
                returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise RuntimeBridgeError("Antigravity CLI 流式执行超时") from exc
            readers[1].join(timeout=max(0, deadline - time.monotonic()))
            if readers[1].is_alive():
                raise RuntimeBridgeError("Antigravity CLI 流式诊断读取超时")
            if returncode:
                raise RuntimeBridgeError(f"Antigravity CLI 退出码 {returncode}")
            if result is None:
                raise RuntimeBridgeError("Antigravity CLI 流式输出缺少 result 事件")
            return result, diagnostics[0]
        except RuntimeBridgeError as exc:
            failure = exc
            raise
        finally:
            stopped.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=1)
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()
            if failure is not None and diagnostics[0].strip():
                failure.args = (f"{failure}\nCLI 诊断: {diagnostics[0].strip()[-8192:]}",)

    def list_models(self):
        raw = self.invoke(["models"], timeout_seconds=30)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return {"models": lines, "raw": raw.rstrip()}


def default_sidecar_endpoint_file(env=None):
    env = os.environ if env is None else env
    override = env.get("CODEX_DYNAMIC_BRIDGE_SIDECAR_ENDPOINT_FILE")
    if override:
        return Path(override).expanduser()
    home = Path(env.get("USERPROFILE", Path.home()))
    return (
        home
        / ".gemini"
        / "antigravity"
        / "sidecar_data"
        / "codex-dynamic-bridge"
        / "codex-bridge"
        / "data"
        / "endpoint.json"
    ).resolve()


class SidecarClient:
    def __init__(self, endpoint_file=None, opener=None):
        self.endpoint_file = Path(endpoint_file or default_sidecar_endpoint_file())
        self.opener = opener or urlopen

    def configuration(self):
        if not self.endpoint_file.is_file():
            raise RuntimeBridgeError(f"未找到 Sidecar 端点文件: {self.endpoint_file}")
        try:
            value = json.loads(self.endpoint_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeBridgeError("Sidecar 端点文件无效") from exc
        if not isinstance(value, dict) or not value.get("url") or not value.get("token"):
            raise RuntimeBridgeError("Sidecar 端点文件缺少 url 或 token")
        if not value["url"].startswith("http://127.0.0.1:"):
            raise RuntimeBridgeError("Sidecar 仅允许绑定 127.0.0.1")
        return value

    def request(self, method, path, payload=None, timeout_seconds=30):
        config = self.configuration()
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            config["url"].rstrip("/") + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {config['token']}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (OSError, HTTPError, URLError) as exc:
            raise RuntimeBridgeError(f"Sidecar 请求失败: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeBridgeError("Sidecar 未返回有效 JSON") from exc

    def health(self):
        return self.request("GET", "/v1/health")

    def new_conversation(self, prompt):
        return self.request("POST", "/v1/conversations", {"prompt": prompt})

    def send_message(self, conversation_id, prompt):
        return self.request(
            "POST",
            f"/v1/conversations/{conversation_id}/messages",
            {"prompt": prompt},
        )

    def event_page(self, conversation_id=None, limit=100, after=None, stream_id=None):
        if not 1 <= limit <= 1000 or (after is not None and after < 0):
            raise RuntimeBridgeError("limit 必须为 1..1000，after 必须为非负整数")
        query = {"limit": limit}
        if after is not None:
            query["after"] = after
        if stream_id is not None:
            query["stream_id"] = stream_id
        if conversation_id:
            if not all(character.isalnum() or character == "-" for character in conversation_id):
                raise RuntimeBridgeError("conversation ID 只能包含字母、数字和连字符")
            query["conversation_id"] = conversation_id
        page = self.request("GET", f"/v1/events?{urlencode(query)}")
        if (
            not isinstance(page, dict)
            or not isinstance(page.get("events"), list)
            or not isinstance(page.get("nextCursor"), int)
            or not isinstance(page.get("hasMore"), bool)
        ):
            raise RuntimeBridgeError("Sidecar 事件分页响应无效")
        return page

    def list_events(self, conversation_id=None, limit=100):
        return self.event_page(conversation_id, limit=limit)["events"]

    def list_all_events(self, conversation_id=None, page_size=1000):
        events = []
        cursor = 0
        while True:
            page = self.event_page(conversation_id, limit=page_size, after=cursor)
            events.extend(page["events"])
            if not page.get("hasMore"):
                return events
            next_cursor = page.get("nextCursor")
            if not isinstance(next_cursor, int) or next_cursor <= cursor:
                raise RuntimeBridgeError("Sidecar 事件游标未向前推进")
            cursor = next_cursor

    def wait(self, conversation_id, timeout_seconds=30, poll_seconds=0.5, after=None):
        deadline = time.monotonic() + timeout_seconds
        while True:
            event = completed_event(self.list_events(conversation_id), after)
            if event:
                return event
            if time.monotonic() >= deadline:
                raise RuntimeBridgeError(f"等待 Sidecar 会话完成超时: {conversation_id}")
            time.sleep(poll_seconds)

    def wait_for_event(
        self,
        conversation_id,
        kind,
        timeout_seconds=30,
        poll_seconds=0.5,
        tool_name=None,
        approval_state=None,
        after=None,
    ):
        deadline = time.monotonic() + timeout_seconds
        while True:
            for event in reversed(self.list_events(conversation_id)):
                if event.get("kind") != kind:
                    continue
                if tool_name and event.get("toolName") != tool_name:
                    continue
                if approval_state and event.get("approvalState") != approval_state:
                    continue
                if not after_event(event, after):
                    continue
                return event
            if time.monotonic() >= deadline:
                raise RuntimeBridgeError(
                    f"等待 Sidecar 事件超时: {conversation_id} / {kind}"
                )
            time.sleep(poll_seconds)

    def list_schedules(self):
        return self.request("GET", "/v1/schedules").get("schedules", [])

    def create_schedule(self, prompt, interval_seconds, conversation_id=None):
        return self.request(
            "POST",
            "/v1/schedules",
            {
                "prompt": prompt,
                "intervalSeconds": interval_seconds,
                "conversationId": conversation_id,
            },
        )

    def remove_schedule(self, schedule_id):
        if not all(character.isalnum() or character == "-" for character in schedule_id):
            raise RuntimeBridgeError("schedule ID 只能包含字母、数字和连字符")
        return self.request("DELETE", f"/v1/schedules/{schedule_id}")


def runtime_summary():
    try:
        import playwright

        playwright_available = playwright is not None
    except ImportError:
        playwright_available = False
    agy = find_agy()
    endpoint = default_sidecar_endpoint_file()
    return {
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "playwright": {"available": playwright_available},
        "agy": AgyClient(executable=agy).capabilities() if agy else {
            "available": False, "path": None, "version": None, "flags": {},
        },
        "sidecar": {
            "configured": endpoint.is_file(),
            "endpointFile": str(endpoint),
        },
    }
