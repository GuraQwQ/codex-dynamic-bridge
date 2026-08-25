import json
import os
import shutil
import subprocess
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bridge.state import atomic_write_json


SIDECAR_ID = "codex-dynamic-bridge/codex-bridge"
DEFAULT_PROJECT_ID = "default-cli-project"


class CompanionError(RuntimeError):
    """Antigravity Companion 全局注册失败。"""


def config_root(env=None):
    env = os.environ if env is None else env
    override = env.get("CODEX_DYNAMIC_BRIDGE_ANTIGRAVITY_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    home = Path(env.get("USERPROFILE", Path.home()))
    return (home / ".gemini" / "config").resolve()


def source_plugin():
    return Path(__file__).resolve().parent.parent / "companion" / "antigravity-plugin"


def destination_plugin(env=None):
    return config_root(env) / "plugins" / "codex-dynamic-bridge"


def endpoint_file(env=None):
    env = os.environ if env is None else env
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


def desktop_running(env=None):
    env = os.environ if env is None else env
    appdata = env.get("APPDATA")
    if not appdata:
        return False
    port_file = Path(appdata) / "Antigravity" / "DevToolsActivePort"
    if not port_file.is_file():
        return False
    try:
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0].strip())
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1):
            return True
    except (OSError, ValueError, IndexError):
        return False


def endpoint_ready(env=None):
    path = endpoint_file(env)
    try:
        endpoint = json.loads(path.read_text(encoding="utf-8"))
        url = endpoint["url"].rstrip("/")
        token = endpoint["token"]
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        if not isinstance(token, str) or not token:
            return False
        request = Request(
            f"{url}/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urlopen(request, timeout=1) as response:
            return response.status == 200
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def load_config(path):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CompanionError(f"Antigravity 配置不是有效 JSON，未修改: {path}") from exc
    if not isinstance(value, dict):
        raise CompanionError("Antigravity 配置根节点必须是对象")
    return value


def validate_owned_destination(destination):
    expected = destination_plugin({"CODEX_DYNAMIC_BRIDGE_ANTIGRAVITY_CONFIG": str(destination.parent.parent)})
    if destination.resolve() != expected.resolve():
        raise CompanionError(f"全局插件目标路径不符合预期: {destination}")
    if destination.is_symlink():
        raise CompanionError("全局 Companion 目标不能是符号链接")
    marker = destination / "plugin.json"
    if destination.exists():
        if not destination.is_dir() or not marker.is_file():
            raise CompanionError("目标目录不是可识别的 Codex Dynamic Bridge Companion，拒绝覆盖")
        try:
            manifest = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CompanionError("现有 Companion plugin.json 无效，拒绝覆盖") from exc
        if not isinstance(manifest, dict):
            raise CompanionError("现有 Companion plugin.json 根节点必须是对象")
        if manifest.get("name") != "codex-dynamic-bridge":
            raise CompanionError("目标目录属于其他 Antigravity 插件，拒绝覆盖")


def hook_command(arguments):
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    import shlex

    return shlex.join(arguments)


def write_hooks(destination, endpoint):
    sink = destination / "sidecars" / "codex-bridge" / "event_sink.py"
    events = {}
    for kind in ("PreToolUse", "PostToolUse", "PostInvocation", "Stop"):
        entry = {
            "type": "command",
            "command": hook_command(
                [sys.executable, str(sink), kind, "--endpoint-file", str(endpoint)]
            ),
            "timeout": 10,
        }
        if kind in {"PreToolUse", "PostToolUse"}:
            matcher = "run_command|ask_permission" if kind == "PreToolUse" else "*"
            events[kind] = [{"matcher": matcher, "hooks": [entry]}]
        else:
            events[kind] = [entry]
    hooks = {"codex-dynamic-bridge-events": events}
    atomic_write_json(destination / "hooks.json", hooks)


def validate_config(config):
    sidecars = config.get("sidecars")
    if sidecars is None:
        return
    if not isinstance(sidecars, dict):
        raise CompanionError("Antigravity config.json 的 sidecars 必须是对象")
    existing = sidecars.get(SIDECAR_ID)
    if existing is not None and not isinstance(existing, dict):
        raise CompanionError(f"Sidecar 配置必须是对象: {SIDECAR_ID}")


def staged_plugin(destination, endpoint=None):
    source = source_plugin()
    if not (source / "plugin.json").is_file():
        raise CompanionError(f"找不到 Companion 源码: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    shutil.copytree(
        source,
        stage,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data"),
    )
    return stage


def source_matches(destination):
    source = source_plugin()
    def files(root, generated_hooks=False):
        return {
            path.relative_to(root): path
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and "data" not in path.relative_to(root).parts
            and path.suffix != ".pyc"
            and not (generated_hooks and path.relative_to(root) == Path("hooks.json"))
        }

    source_files = files(source)
    installed_files = files(destination, generated_hooks=True)
    return source_files.keys() == installed_files.keys() and all(
        installed_files[relative].read_bytes() == path.read_bytes()
        for relative, path in source_files.items()
    )


def status(env=None):
    root = config_root(env)
    destination = destination_plugin(env)
    config_path = root / "config.json"
    config = load_config(config_path)
    validate_config(config)
    entry = config.get("sidecars", {}).get(SIDECAR_ID, {})
    return {
        "installed": (destination / "plugin.json").is_file(),
        "enabled": entry.get("enabled") is True,
        "projectId": entry.get("projectId"),
        "destination": str(destination),
        "configPath": str(config_path),
        "endpointFile": str(endpoint_file(env)),
        "endpointFileExists": endpoint_file(env).is_file(),
        "endpointReady": endpoint_ready(env),
        "antigravityRunning": desktop_running(env),
    }


def install_global(project_id, env=None):
    if not project_id or not project_id.strip():
        raise CompanionError("全局 Sidecar 必须配置默认 project ID")
    root = config_root(env)
    config_path = root / "config.json"
    config = load_config(config_path)
    validate_config(config)

    destination = destination_plugin(env)
    validate_owned_destination(destination)
    updated = deepcopy(config)
    sidecars = updated.setdefault("sidecars", {})
    existing = sidecars.get(SIDECAR_ID, {})
    sidecars[SIDECAR_ID] = {
        **existing,
        "enabled": True,
        "projectId": project_id.strip(),
    }

    if destination.exists() and source_matches(destination) and updated == config:
        result = status(env)
        result.update({"updated": False, "restartRequired": False})
        return result
    if destination.exists() and desktop_running(env):
        raise CompanionError("Antigravity 正在运行且 Companion 有更新；请完全退出后重试安装")

    stage = staged_plugin(destination, endpoint_file(env))
    backup = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.backup"
    had_destination = destination.exists()
    moved_destination = False
    installed_stage = False
    try:
        if had_destination:
            os.replace(destination, backup)
            moved_destination = True
        os.replace(stage, destination)
        installed_stage = True
        # Hook 命令必须引用最终稳定目录；暂存目录会在事务结束时删除。
        write_hooks(destination, endpoint_file(env))
        atomic_write_json(config_path, updated)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if installed_stage and destination.exists():
            shutil.rmtree(destination)
        if moved_destination and backup.exists():
            os.replace(backup, destination)
        raise
    if had_destination:
        shutil.rmtree(backup, ignore_errors=True)

    result = status(env)
    result["updated"] = True
    result["restartRequired"] = result["antigravityRunning"]
    return result


def uninstall_global(env=None):
    root = config_root(env)
    destination = destination_plugin(env)
    validate_owned_destination(destination)
    config_path = root / "config.json"
    config = load_config(config_path)
    validate_config(config)
    updated = deepcopy(config)
    sidecars = updated.get("sidecars")
    configured = isinstance(sidecars, dict) and SIDECAR_ID in sidecars
    if isinstance(sidecars, dict):
        sidecars.pop(SIDECAR_ID, None)
    installed = destination.exists()
    backup = None
    try:
        if installed:
            backup = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.backup"
            os.replace(destination, backup)
        if configured:
            atomic_write_json(config_path, updated)
    except Exception:
        if backup and backup.exists():
            os.replace(backup, destination)
        raise
    if backup:
        shutil.rmtree(backup, ignore_errors=True)

    result = status(env)
    result["removed"] = installed or configured
    result["restartRequired"] = result["antigravityRunning"]
    return result
