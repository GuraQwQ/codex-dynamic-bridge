import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    def __init__(self, executable=None, runner=None):
        self.executable = executable or find_agy()
        self.runner = runner or subprocess.run
        if not self.executable:
            raise RuntimeBridgeError(
                "未找到 Antigravity CLI；设置 CODEX_DYNAMIC_BRIDGE_AGY 或安装 agy"
            )

    def invoke(self, arguments, timeout_seconds=300):
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
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeBridgeError(f"无法执行 Antigravity CLI: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "未知错误").strip()
            raise RuntimeBridgeError(
                f"Antigravity CLI 退出码 {result.returncode}: {detail[:1000]}"
            )
        return result.stdout

    def run_prompt(
        self,
        prompt,
        conversation_id=None,
        project_id=None,
        model=None,
        effort=None,
        agent=None,
        timeout_seconds=300,
    ):
        arguments = ["-p", prompt, "--output-format", "json"]
        if conversation_id:
            arguments.append(f"--conversation={conversation_id}")
        if project_id:
            arguments.append(f"--project={project_id}")
        if model:
            arguments.extend(["--model", model])
        if effort:
            arguments.extend(["--effort", effort])
        if agent:
            arguments.extend(["--agent", agent])
        arguments.extend(["--print-timeout", f"{timeout_seconds}s"])
        raw = self.invoke(arguments, timeout_seconds=timeout_seconds + 10)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeBridgeError("Antigravity CLI 未返回有效 JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeBridgeError("Antigravity CLI JSON 根节点必须是对象")
        if result.get("status") != "SUCCESS":
            raise RuntimeBridgeError(result.get("error") or "Antigravity 任务未成功完成")
        return result

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

    def list_events(self, conversation_id=None):
        path = "/v1/events"
        if conversation_id:
            if not all(character.isalnum() or character == "-" for character in conversation_id):
                raise RuntimeBridgeError("conversation ID 只能包含字母、数字和连字符")
            path += f"?conversation_id={conversation_id}"
        return self.request("GET", path).get("events", [])

    def wait(self, conversation_id, timeout_seconds=30, poll_seconds=0.5):
        deadline = time.monotonic() + timeout_seconds
        while True:
            for event in reversed(self.list_events(conversation_id)):
                if event.get("kind") == "Stop" and event.get("fullyIdle") is True:
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
                if after and event.get("observedAt", "") <= after:
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
        "agy": {"available": bool(agy), "path": agy},
        "sidecar": {
            "configured": endpoint.is_file(),
            "endpointFile": str(endpoint),
        },
    }
