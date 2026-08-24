import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from bridge.runtime import (
    AgyClient,
    RuntimeBridgeError,
    SidecarClient,
    find_agy,
    runtime_summary,
)
from bridge.setup import SetupError, default_agy_install_dir, ensure_agy
from bridge.companion import (
    CompanionError,
    DEFAULT_PROJECT_ID,
    install_global as install_companion_global,
    status as companion_status,
    uninstall_global as uninstall_companion_global,
)
from bridge.state import (
    EventStore,
    StateError,
    TaskStore,
    artifact_root_for_conversation,
    list_artifacts,
    read_artifact,
)


REQUIRED_FIELDS = ("id", "title", "url", "source", "updatedAt")
ALLOWED_URL_SCHEMES = {"http", "https", "codex"}


class BridgeError(RuntimeError):
    """可直接展示给用户的桥接错误。"""


def configure_standard_streams():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="strict")


def default_links_path():
    override = os.environ.get("CODEX_DYNAMIC_BRIDGE_STORE")
    if override:
        return Path(override).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "plugins" / "data" / "codex-dynamic-bridge" / "links.json"


LINKS_PATH = default_links_path()


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise BridgeError("updatedAt 必须是带时区的 ISO 8601 时间戳")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BridgeError(f"无效的 updatedAt: {value}") from exc
    if parsed.tzinfo is None:
        raise BridgeError("updatedAt 必须包含时区，例如 2026-08-21T08:00:00Z")
    return parsed.astimezone(timezone.utc)


def format_timestamp(value):
    value = value.astimezone(timezone.utc)
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def normalize_link(link, position=None):
    location = f"第 {position} 条记录" if position is not None else "记录"
    if not isinstance(link, dict):
        raise BridgeError(f"{location}必须是 JSON 对象")

    normalized = {}
    for field in REQUIRED_FIELDS:
        value = link.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BridgeError(f"{location}的 {field} 必须是非空字符串")
        normalized[field] = value.strip()

    parsed_url = urlparse(normalized["url"])
    if parsed_url.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise BridgeError(f"{location}的 url 仅支持 http、https 或 codex 协议")
    if parsed_url.scheme.lower() in {"http", "https"} and not parsed_url.netloc:
        raise BridgeError(f"{location}的 url 缺少主机名")
    if parsed_url.scheme.lower() == "codex" and not (parsed_url.netloc or parsed_url.path):
        raise BridgeError(f"{location}的 codex url 缺少目标")

    normalized["updatedAt"] = format_timestamp(parse_timestamp(normalized["updatedAt"]))
    return normalized


def normalize_links(value):
    if not isinstance(value, list):
        raise BridgeError("链接文件的根节点必须是 JSON 数组")
    return [normalize_link(link, index) for index, link in enumerate(value, start=1)]


def load_links(path=None):
    path = Path(path or LINKS_PATH)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as stream:
            return normalize_links(json.load(stream))
    except json.JSONDecodeError as exc:
        raise BridgeError(f"链接文件不是有效 JSON，已保留原文件: {path}") from exc


def save_links(links, path=None):
    path = Path(path or LINKS_PATH)
    normalized = normalize_links(links)
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
            json.dump(normalized, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def merge_latest(existing, incoming):
    """按 id 合并，使用解析后的绝对时间选择最新记录。"""
    existing = normalize_links(existing)
    incoming = normalize_links(incoming)
    by_id = {link["id"]: link for link in existing}
    for link in incoming:
        old = by_id.get(link["id"])
        if old is None or parse_timestamp(link["updatedAt"]) > parse_timestamp(old["updatedAt"]):
            by_id[link["id"]] = link
    return sorted(
        by_id.values(),
        key=lambda link: (parse_timestamp(link["updatedAt"]), link["id"]),
        reverse=True,
    )


def add_link(args):
    link = normalize_link(
        {
            "id": args.id,
            "title": args.title,
            "url": args.url,
            "source": args.source,
            "updatedAt": args.updatedAt,
        }
    )
    existing = load_links()
    merged = merge_latest(existing, [link])
    save_links(merged)
    saved = next(item for item in merged if item["id"] == link["id"])
    changed = saved == link and link not in existing
    result = {"changed": changed, "link": saved}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def list_links(_):
    links = load_links()
    print(json.dumps(links, ensure_ascii=False, indent=2))
    return links


def remove_link(args):
    links = load_links()
    remaining = [link for link in links if link["id"] != args.id]
    if len(remaining) == len(links):
        raise BridgeError(f"未找到链接: {args.id}")
    save_links(remaining)
    result = {"removed": args.id, "total": len(remaining)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def sync_links(args):
    source_path = Path(args.source)
    try:
        with source_path.open("r", encoding="utf-8") as stream:
            source_links = normalize_links(json.load(stream))
    except json.JSONDecodeError as exc:
        raise BridgeError(f"同步源不是有效 JSON: {source_path}") from exc
    existing = load_links()
    merged = merge_latest(existing, source_links)
    save_links(merged)
    result = {
        "read": len(source_links),
        "changed": sum(
            1
            for link in merged
            if next((old for old in existing if old["id"] == link["id"]), None) != link
        ),
        "total": len(merged),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def read_antigravity_port():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise BridgeError("APPDATA 环境变量不可用")
    port_file = Path(appdata) / "Antigravity" / "DevToolsActivePort"
    if not port_file.exists():
        raise BridgeError(f"未找到 Antigravity 调试端口文件: {port_file}")
    lines = port_file.read_text(encoding="utf-8").splitlines()
    try:
        port = int(lines[0].strip())
    except (IndexError, ValueError) as exc:
        raise BridgeError(f"Antigravity 调试端口文件无效: {port_file}") from exc
    if not 1 <= port <= 65535:
        raise BridgeError(f"Antigravity 调试端口超出范围: {port}")
    return port


def fetch_antigravity_pages():
    port = read_antigravity_port()
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            pages = json.load(response)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise BridgeError(f"无法读取 Antigravity 本地调试接口: {exc}") from exc
    if not isinstance(pages, list):
        raise BridgeError("Antigravity 调试接口返回了非预期数据")
    return pages


def conversation_id_from_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0] != "c":
        return None
    return path_parts[1]


def discover_sessions(pages=None):
    pages = fetch_antigravity_pages() if pages is None else pages
    sessions = []
    seen = set()
    for page in pages:
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        url = page.get("url", "")
        conversation_id = conversation_id_from_url(url)
        if not conversation_id or conversation_id in seen:
            continue
        seen.add(conversation_id)
        sessions.append(
            {
                "conversationId": conversation_id,
                "devtoolsId": page.get("id", ""),
                "title": page.get("title") or "Antigravity 会话",
                "url": url,
            }
        )
    return sessions


def discover_app_pages(pages=None):
    pages = fetch_antigravity_pages() if pages is None else pages
    discovered = []
    seen = set()
    for page in pages:
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        devtools_id = page.get("id")
        url = page.get("url", "")
        title = page.get("title", "")
        if not isinstance(devtools_id, str) or not devtools_id:
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            continue
        conversation_id = conversation_id_from_url(url)
        is_shell = parsed.path in {"", "/"} and title.strip() == "Antigravity"
        if not conversation_id and not is_shell:
            continue
        if devtools_id in seen:
            continue
        seen.add(devtools_id)
        discovered.append(
            {
                "kind": "conversation" if conversation_id else "shell",
                "conversationId": conversation_id,
                "devtoolsId": devtools_id,
                "title": title or "Antigravity",
                "url": url,
            }
        )
    return discovered


def select_app_page(pages, target_id=None):
    if target_id:
        matches = [
            page
            for page in pages
            if target_id in {page["devtoolsId"], page.get("conversationId")}
        ]
        if len(matches) != 1:
            raise BridgeError(f"未找到唯一 Antigravity 页面目标: {target_id}")
        return matches[0]
    if not pages:
        raise BridgeError("未找到 Antigravity 客户端页面")
    if len(pages) > 1:
        choices = "、".join(page["devtoolsId"] for page in pages)
        raise BridgeError(f"发现多个 Antigravity 页面，无法选择 bootstrap 目标: {choices}")
    return pages[0]


def discover_links(_):
    sessions = discover_sessions()
    print(json.dumps(sessions, ensure_ascii=False, indent=2))
    return sessions


def discover_pages(_):
    pages = discover_app_pages()
    print(json.dumps(pages, ensure_ascii=False, indent=2))
    return pages


def select_sessions(sessions, conversation_id=None, all_sessions=False):
    if all_sessions:
        if not sessions:
            raise BridgeError("未找到 Antigravity 会话页面")
        return sessions
    if conversation_id:
        selected = [
            session
            for session in sessions
            if conversation_id in {session["conversationId"], session["devtoolsId"]}
        ]
        if not selected:
            raise BridgeError(f"未找到指定 Antigravity 会话: {conversation_id}")
        return selected
    if not sessions:
        raise BridgeError("未找到 Antigravity 会话页面")
    if len(sessions) > 1:
        choices = "、".join(session["conversationId"] for session in sessions)
        raise BridgeError(f"发现多个会话，无法判断前台会话；请使用 --id 选择: {choices}")
    return sessions


def live_link(args):
    sessions = select_sessions(discover_sessions(), args.id, args.all)
    observed_at = format_timestamp(datetime.now(timezone.utc))
    links = [
        {
            "id": f"antigravity:{session['conversationId']}",
            "title": session["title"],
            "url": session["url"],
            "source": "Antigravity",
            "updatedAt": observed_at,
        }
        for session in sessions
    ]
    merged = merge_latest(load_links(), links)
    save_links(merged)
    output = links[0] if len(links) == 1 else links
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


def control_page(args):
    from bridge.control import ControlError, MUTATING_ACTIONS, execute_control

    if args.control_action in MUTATING_ACTIONS and not args.confirm_control:
        raise BridgeError(
            f"{args.control_action} 会修改页面；仅在用户明确授权该动作后传入 --confirm-control"
        )
    if args.control_action in {"fill", "fill-role"} and args.text_stdin:
        args.text = sys.stdin.read()

    session = select_sessions(discover_sessions(), args.id)[0]
    try:
        result = execute_control(read_antigravity_port(), session["conversationId"], args)
    except ControlError as exc:
        raise BridgeError(str(exc)) from exc
    result["conversationId"] = session["conversationId"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return value


def doctor_command(_):
    result = runtime_summary()
    try:
        result["companion"] = companion_status()
    except CompanionError as exc:
        result["companion"] = {"statusError": str(exc)}
    try:
        port = read_antigravity_port()
        pages = fetch_antigravity_pages()
        sessions = discover_sessions(pages)
        app_pages = discover_app_pages(pages)
        result["desktop"] = {
            "available": True,
            "port": port,
            "sessions": len(sessions),
            "pages": len(app_pages),
            "bootstrapTargets": sum(page["kind"] == "shell" for page in app_pages),
        }
    except BridgeError as exc:
        result["desktop"] = {"available": False, "error": str(exc)}

    if result["sidecar"]["configured"]:
        try:
            result["sidecar"]["health"] = SidecarClient().health()
            result["sidecar"]["available"] = True
        except RuntimeBridgeError as exc:
            result["sidecar"]["available"] = False
            result["sidecar"]["error"] = str(exc)
    else:
        result["sidecar"]["available"] = False
    return print_json(result)


def prompt_from_args(args):
    if getattr(args, "prompt_stdin", False):
        value = sys.stdin.read()
    else:
        value = args.prompt
    if not value or not value.strip():
        raise BridgeError("提示词不能为空")
    return value


def select_runtime_backend(name, require_agy=False):
    if require_agy and name == "sidecar":
        raise BridgeError("Sidecar 不支持 model、effort、agent 或任意 project 参数；请使用 agy")
    if name == "sidecar":
        client = SidecarClient()
        client.health()
        return "sidecar", client
    if name == "agy":
        return "agy", AgyClient()

    if require_agy:
        if find_agy():
            return "agy", AgyClient()
        raise BridgeError("该请求需要 Antigravity CLI，但未找到 agy")

    endpoint = SidecarClient()
    try:
        endpoint.health()
        return "sidecar", endpoint
    except RuntimeBridgeError:
        if find_agy():
            return "agy", AgyClient()
    raise BridgeError(
        "没有可用的稳定写后端；请启用 Companion Sidecar 或设置 CODEX_DYNAMIC_BRIDGE_AGY"
    )


def conversation_command(args):
    event_store = EventStore()
    task_store = TaskStore()
    if args.conversation_action == "wait":
        if args.backend in {"auto", "sidecar"}:
            try:
                sidecar = SidecarClient()
                sidecar.health()
                return print_json(
                    sidecar.wait(
                        args.conversation_id,
                        timeout_seconds=args.timeout_seconds,
                    )
                )
            except RuntimeBridgeError:
                if args.backend == "sidecar":
                    raise
        return print_json(
            event_store.wait(args.conversation_id, timeout_seconds=args.timeout_seconds)
        )

    prompt = prompt_from_args(args)
    if args.conversation_action in {"send", "resume"} and args.backend == "auto":
        try:
            select_sessions(discover_sessions(), args.conversation_id)
        except BridgeError:
            pass
        else:
            args.id = args.conversation_id
            args.timeout_ms = min(args.timeout_seconds * 1000, 30000)
            args.settle_ms = 250
            args.confirm_send = True
            args.prompt = prompt
            args.conversation_action = "send-now"
            return desktop_conversation_command(args)
    require_agy = bool(
        args.model
        or args.effort
        or args.agent
        or getattr(args, "project_id", None)
        or getattr(args, "project_path", None)
        or getattr(args, "new_project", False)
        or args.conversation_action == "resume"
    )
    backend_name = args.backend
    if (
        args.conversation_action in {"send", "resume"}
        and backend_name == "auto"
        and find_agy()
    ):
        backend_name = "agy"
    backend, client = select_runtime_backend(backend_name, require_agy=require_agy)
    if args.conversation_action == "new":
        if not args.confirm_create:
            raise BridgeError("新建会话会修改 Antigravity；请在明确授权后传入 --confirm-create")
        if backend == "sidecar":
            result = client.new_conversation(prompt)
        else:
            result = client.run_prompt(
                prompt,
                project_id=args.project_id,
                model=args.model,
                effort=args.effort,
                agent=args.agent,
                timeout_seconds=args.timeout_seconds,
                project_path=getattr(args, "project_path", None),
                new_project=getattr(args, "new_project", False),
            )
    else:
        if not args.confirm_send:
            raise BridgeError("发送消息会修改会话；请在明确授权后传入 --confirm-send")
        if backend == "sidecar":
            result = client.send_message(args.conversation_id, prompt)
        else:
            result = client.run_prompt(
                prompt,
                conversation_id=args.conversation_id,
                model=args.model,
                effort=args.effort,
                agent=args.agent,
                timeout_seconds=args.timeout_seconds,
                project_path=getattr(args, "project_path", None),
            )

    conversation_id = result.get("conversation_id") or result.get("conversationId")
    if conversation_id:
        task = task_store.upsert(
            {
                "conversationId": conversation_id,
                "projectId": getattr(args, "project_id", None),
                "model": args.model,
                "status": result.get("status", "submitted").lower(),
            }
        )
    else:
        task = None
    return print_json({"backend": backend, "result": result, "task": task})


def model_command(args):
    if args.model_action == "list":
        return print_json(AgyClient().list_models())
    raise BridgeError(f"不支持的模型动作: {args.model_action}")


def event_command(args):
    store = EventStore()
    tasks = TaskStore()
    if args.event_action == "sync":
        imported = store.import_events(SidecarClient().list_events(args.conversation_id))
        synced_tasks = [tasks.sync_event(event) for event in imported]
        return print_json({"imported": len(imported), "tasks": synced_tasks})
    if args.event_action == "ingest":
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise BridgeError("Hook 标准输入不是有效 JSON") from exc
        event = store.append(args.kind, payload)
        task = tasks.sync_event(event)
        return print_json({"event": event, "task": task})
    if args.event_action == "list":
        return print_json(store.list(args.conversation_id, limit=args.limit))
    if args.event_action == "wait-approval":
        after = format_timestamp(parse_timestamp(args.after)) if args.after else None
        if args.backend in {"auto", "sidecar"}:
            sidecar = SidecarClient()
            try:
                event = sidecar.wait_for_event(
                    args.conversation_id,
                    "PreToolUse",
                    timeout_seconds=args.timeout_seconds,
                    tool_name=args.tool_name,
                    approval_state="requested",
                    after=after,
                )
            except RuntimeBridgeError:
                if args.backend == "sidecar":
                    raise
            else:
                store.import_events([event])
                tasks.sync_event(event)
                return print_json(event)
        return print_json(
            store.wait_approval(
                args.conversation_id,
                timeout_seconds=args.timeout_seconds,
                tool_name=args.tool_name,
                after=after,
            )
        )
    return print_json(
        store.wait(args.conversation_id, timeout_seconds=args.timeout_seconds)
    )


def approval_command(args):
    if args.approval_action == "inspect":
        return run_desktop_workflow(args, "approval-inspect")
    if not args.confirm_approval:
        raise BridgeError("响应命令审批会修改 Antigravity 状态；请传入 --confirm-approval")

    observed_at = format_timestamp(parse_timestamp(args.event_observed_at))
    events = SidecarClient().list_events(args.id)
    matching = [
        event
        for event in events
        if event.get("kind") == "PreToolUse"
        and event.get("approvalState") == "requested"
        and event.get("toolName") == args.tool_name
        and event.get("observedAt") == observed_at
    ]
    if len(matching) != 1:
        raise BridgeError("找不到与本次响应精确对应的唯一审批事件，拒绝点击")
    return run_desktop_workflow(args, "approval-respond")


def review_command(args):
    return run_desktop_workflow(args, "review-changes")


def schedule_command(args):
    client = SidecarClient()
    client.health()
    if args.schedule_action == "list":
        return print_json(client.list_schedules())
    if args.schedule_action == "remove":
        if not args.confirm_schedule:
            raise BridgeError("删除定时任务会修改 Sidecar 状态；请传入 --confirm-schedule")
        return print_json(client.remove_schedule(args.schedule_id))
    if not args.confirm_schedule:
        raise BridgeError("创建定时任务会持续发送消息；请传入 --confirm-schedule")
    return print_json(
        client.create_schedule(
            prompt_from_args(args),
            args.interval_seconds,
            conversation_id=args.conversation_id,
        )
    )


def activity_command(args):
    return print_json(EventStore().summary(args.conversation_id))


def companion_command(args):
    if args.companion_action == "status":
        return print_json(companion_status())
    if args.companion_action == "install-global":
        if not args.confirm_install:
            raise BridgeError("全局注册会修改 Antigravity 配置；请传入 --confirm-install")
        return print_json(install_companion_global(args.project_id))
    if not args.confirm_uninstall:
        raise BridgeError("卸载会删除全局 Companion；请传入 --confirm-uninstall")
    return print_json(uninstall_companion_global())


def setup_command(args):
    if args.setup_action == "status":
        return print_json(
            {
                "agy": {
                    "available": bool(find_agy()),
                    "path": find_agy(),
                    "defaultInstallDir": str(default_agy_install_dir()),
                },
                "companion": companion_status(),
            }
        )
    if not args.confirm_setup:
        raise BridgeError("完整能力装载会安装 agy 并修改 Antigravity 配置；请传入 --confirm-setup")
    agy = ensure_agy(
        install_dir=args.agy_dir,
        allow_system_drive=args.allow_system_drive,
    )
    companion = install_companion_global(args.project_id)
    return print_json({"agy": agy, "companion": companion})


def task_command(args):
    store = TaskStore()
    if args.task_action == "list":
        return print_json(store.load())
    if args.task_action == "remove":
        return print_json(store.remove(args.conversation_id))
    record = {
        "conversationId": args.conversation_id,
        "codexTaskId": args.codex_task_id,
        "projectId": args.project_id,
        "title": args.title,
        "url": args.url,
        "model": args.model,
        "status": args.status,
    }
    return print_json(store.upsert(record))


def artifact_command(args):
    root = artifact_root_for_conversation(EventStore(), args.conversation_id)
    if args.artifact_action == "list":
        return print_json(
            {"root": str(root), "artifacts": list_artifacts(root, limit=args.limit)}
        )
    content = read_artifact(root, args.path, max_bytes=args.max_bytes)
    return print_json({"root": str(root), "path": args.path, "content": content})


def run_desktop_workflow(args, workflow_action):
    from bridge.control import ControlError, execute_control

    args.control_action = "workflow"
    args.workflow_action = workflow_action
    session = select_sessions(discover_sessions(), args.id)[0]
    try:
        result = execute_control(read_antigravity_port(), session["conversationId"], args)
    except ControlError as exc:
        raise BridgeError(str(exc)) from exc
    result["conversationId"] = session["conversationId"]
    return print_json(result)


def run_new_conversation_workflow(args):
    from bridge.control import ControlError, execute_control

    args.control_action = "workflow"
    args.workflow_action = "conversation-new"
    args.prompt = prompt_from_args(args)
    page = select_app_page(discover_app_pages(), args.id)
    try:
        result = execute_control(
            read_antigravity_port(),
            page.get("conversationId"),
            args,
            page_url=page["url"],
        )
    except ControlError as exc:
        raise BridgeError(str(exc)) from exc
    conversation_id = result.get("detail", {}).get("conversationId")
    if not conversation_id:
        raise BridgeError("新建会话动作完成，但未观察到新的 conversation ID；不要自动重试")
    result["conversationId"] = conversation_id
    result["sourceDevtoolsId"] = page["devtoolsId"]
    result["bootstrappedFromShell"] = page["kind"] == "shell"
    return print_json(result)


def desktop_conversation_command(args):
    if args.conversation_action == "send-now":
        if not args.confirm_send:
            raise BridgeError("立即发送补充会中断当前轨迹；请传入 --confirm-send")
        args.prompt = prompt_from_args(args)
        return run_desktop_workflow(args, "conversation-send-now")
    if not args.confirm_conversation:
        raise BridgeError("该会话动作会改变 Antigravity 页面或会话；请传入 --confirm-conversation")
    if args.conversation_action == "open-new":
        return run_new_conversation_workflow(args)
    mapping = {
        "switch": "conversation-switch",
        "rename": "conversation-rename",
        "fork": "conversation-fork",
        "cancel": "conversation-cancel",
    }
    return run_desktop_workflow(args, mapping[args.conversation_action])


def project_command(args):
    if not args.confirm_project:
        raise BridgeError("项目切换或新建向导会改变 Antigravity 页面；请传入 --confirm-project")
    workflow = "project-open" if args.project_action == "open" else "project-new"
    return run_desktop_workflow(args, workflow)


def model_set_command(args):
    if not args.confirm_model:
        raise BridgeError("模型切换会影响后续消息；请传入 --confirm-model")
    return run_desktop_workflow(args, "model-set")


def settings_command(args):
    if args.settings_action == "set" and not args.confirm_settings:
        raise BridgeError("设置修改会持久化；请传入 --confirm-settings")
    workflow = {
        "open": "settings-open",
        "read": "settings-read",
        "set": "settings-set",
    }[args.settings_action]
    return run_desktop_workflow(args, workflow)


def usage_command(args):
    return run_desktop_workflow(args, "usage")


def artifact_proceed_command(args):
    if not args.confirm_artifact:
        raise BridgeError("批准产物会允许 Antigravity 继续执行；请传入 --confirm-artifact")
    return run_desktop_workflow(args, "artifact-proceed")


def add_desktop_workflow_arguments(parser, require_id=True):
    parser.add_argument(
        "--id",
        required=require_id,
        help="会话 conversationId 或 DevTools id",
    )
    parser.add_argument(
        "--timeout-ms",
        type=timeout_milliseconds,
        default=5000,
    )
    parser.add_argument(
        "--settle-ms",
        type=settle_milliseconds,
        default=300,
    )


def timeout_milliseconds(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是整数毫秒") from exc
    if not 100 <= parsed <= 30000:
        raise argparse.ArgumentTypeError("必须在 100 到 30000 毫秒之间")
    return parsed


def settle_milliseconds(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是整数毫秒") from exc
    if not 0 <= parsed <= 5000:
        raise argparse.ArgumentTypeError("必须在 0 到 5000 毫秒之间")
    return parsed


def nonnegative_integer(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是非负整数") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return parsed


def add_control_target_arguments(parser, include_selector=False):
    parser.add_argument("--id", required=True, help="会话 conversationId 或 DevTools id")
    parser.add_argument(
        "--timeout-ms",
        type=timeout_milliseconds,
        default=5000,
        help="动作超时，默认 5000 毫秒",
    )
    if include_selector:
        parser.add_argument("--selector", required=True, help="Playwright 定位器或 CSS 选择器")
        parser.add_argument(
            "--nth",
            type=nonnegative_integer,
            help="多匹配时显式选择从 0 开始的序号",
        )


def add_mutating_control_arguments(parser):
    parser.add_argument(
        "--confirm-control",
        action="store_true",
        help="确认用户已明确授权当前页面修改动作",
    )
    parser.add_argument(
        "--settle-ms",
        type=settle_milliseconds,
        default=250,
        help="动作后等待页面稳定的时间，默认 250 毫秒",
    )


def add_role_target_arguments(parser):
    add_control_target_arguments(parser)
    parser.add_argument("--role", required=True, help="可访问性角色，例如 button、textbox")
    parser.add_argument("--name", required=True, help="可访问名称")
    parser.add_argument(
        "--contains",
        action="store_true",
        help="名称使用包含匹配；默认精确匹配",
    )
    parser.add_argument(
        "--nth",
        type=nonnegative_integer,
        help="多匹配时显式选择从 0 开始的序号",
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="codex-dynamic-bridge",
        description="发现 Antigravity 会话元数据，并管理本地任务链接快照。",
    )
    parser.add_argument(
        "--store",
        type=Path,
        help="覆盖状态文件路径；也可设置 CODEX_DYNAMIC_BRIDGE_STORE",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="探测桌面、Playwright、agy 与 Sidecar 能力")
    doctor_parser.set_defaults(func=doctor_command)

    add_parser = subparsers.add_parser("add", help="添加或按时间更新单条链接")
    add_parser.add_argument("--id", required=True, help="链接唯一标识")
    add_parser.add_argument("--title", required=True, help="任务标题")
    add_parser.add_argument("--url", required=True, help="任务 URL")
    add_parser.add_argument("--source", required=True, help="来源名称")
    add_parser.add_argument("--updatedAt", required=True, help="带时区的 ISO 8601 时间戳")
    add_parser.set_defaults(func=add_link)

    list_parser = subparsers.add_parser("list", help="列出已存储链接")
    list_parser.set_defaults(func=list_links)

    remove_parser = subparsers.add_parser("remove", help="按 id 删除链接")
    remove_parser.add_argument("--id", required=True, help="要删除的链接 id")
    remove_parser.set_defaults(func=remove_link)

    sync_parser = subparsers.add_parser("sync", help="从 JSON 数组合并链接元数据")
    sync_parser.add_argument("--source", required=True, help="同步源 JSON 文件")
    sync_parser.set_defaults(func=sync_links)

    discover_parser = subparsers.add_parser("discover", help="只读列出 Antigravity 会话候选")
    discover_parser.set_defaults(func=discover_links)

    discover_pages_parser = subparsers.add_parser(
        "discover-pages", help="只读列出可信 Antigravity 会话页与客户端外壳页"
    )
    discover_pages_parser.set_defaults(func=discover_pages)

    live_parser = subparsers.add_parser("live", help="读取并保存 Antigravity 会话元数据")
    selection = live_parser.add_mutually_exclusive_group()
    selection.add_argument("--id", help="会话 conversationId 或 DevTools id")
    selection.add_argument("--all", action="store_true", help="保存所有发现的会话")
    live_parser.set_defaults(func=live_link)

    conversation_parser = subparsers.add_parser("conversation", help="新建、继续或等待 Antigravity 会话")
    conversation_subparsers = conversation_parser.add_subparsers(
        dest="conversation_action", required=True
    )

    new_conversation_parser = conversation_subparsers.add_parser("new", help="创建并运行新会话")
    new_prompt = new_conversation_parser.add_mutually_exclusive_group(required=True)
    new_prompt.add_argument("--prompt")
    new_prompt.add_argument("--prompt-stdin", action="store_true")
    new_conversation_parser.add_argument("--backend", choices=("auto", "agy", "sidecar"), default="auto")
    new_conversation_parser.add_argument("--project-id")
    new_conversation_parser.add_argument(
        "--project-path",
        type=Path,
        help="用户项目目录；agy 将以此目录作为工作目录",
    )
    new_conversation_parser.add_argument(
        "--new-project",
        action="store_true",
        help="在 --project-path 中创建 Antigravity 项目",
    )
    new_conversation_parser.add_argument("--model")
    new_conversation_parser.add_argument("--effort", choices=("low", "medium", "high"))
    new_conversation_parser.add_argument("--agent")
    new_conversation_parser.add_argument("--timeout-seconds", type=nonnegative_integer, default=300)
    new_conversation_parser.add_argument("--confirm-create", action="store_true")
    new_conversation_parser.set_defaults(func=conversation_command)

    send_conversation_parser = conversation_subparsers.add_parser("send", help="向现有会话发送消息")
    send_conversation_parser.add_argument("--conversation-id", required=True)
    send_prompt = send_conversation_parser.add_mutually_exclusive_group(required=True)
    send_prompt.add_argument("--prompt")
    send_prompt.add_argument("--prompt-stdin", action="store_true")
    send_conversation_parser.add_argument("--backend", choices=("auto", "agy", "sidecar"), default="auto")
    send_conversation_parser.add_argument("--model")
    send_conversation_parser.add_argument("--effort", choices=("low", "medium", "high"))
    send_conversation_parser.add_argument("--agent")
    send_conversation_parser.add_argument("--timeout-seconds", type=nonnegative_integer, default=300)
    send_conversation_parser.add_argument("--confirm-send", action="store_true")
    send_conversation_parser.set_defaults(func=conversation_command)

    resume_conversation_parser = conversation_subparsers.add_parser(
        "resume", help="恢复已停止会话；优先桌面页面，其次使用 agy"
    )
    resume_conversation_parser.add_argument("--conversation-id", required=True)
    resume_prompt = resume_conversation_parser.add_mutually_exclusive_group(required=True)
    resume_prompt.add_argument("--prompt")
    resume_prompt.add_argument("--prompt-stdin", action="store_true")
    resume_conversation_parser.add_argument("--backend", choices=("auto", "agy"), default="auto")
    resume_conversation_parser.add_argument("--project-path", type=Path)
    resume_conversation_parser.add_argument("--model")
    resume_conversation_parser.add_argument("--effort", choices=("low", "medium", "high"))
    resume_conversation_parser.add_argument("--agent")
    resume_conversation_parser.add_argument("--timeout-seconds", type=nonnegative_integer, default=300)
    resume_conversation_parser.add_argument("--confirm-send", action="store_true")
    resume_conversation_parser.set_defaults(func=conversation_command)

    send_now_conversation_parser = conversation_subparsers.add_parser(
        "send-now", help="将补充内容立即注入正在执行的桌面会话"
    )
    add_desktop_workflow_arguments(send_now_conversation_parser)
    send_now_prompt = send_now_conversation_parser.add_mutually_exclusive_group(
        required=True
    )
    send_now_prompt.add_argument("--prompt")
    send_now_prompt.add_argument("--prompt-stdin", action="store_true")
    send_now_conversation_parser.add_argument("--confirm-send", action="store_true")
    send_now_conversation_parser.set_defaults(func=desktop_conversation_command)

    wait_conversation_parser = conversation_subparsers.add_parser("wait", help="等待 Hook 报告会话完全空闲")
    wait_conversation_parser.add_argument("--conversation-id", required=True)
    wait_conversation_parser.add_argument("--backend", choices=("auto", "local", "sidecar"), default="auto")
    wait_conversation_parser.add_argument("--timeout-seconds", type=nonnegative_integer, default=30)
    wait_conversation_parser.set_defaults(func=conversation_command)

    open_new_parser = conversation_subparsers.add_parser("open-new", help="在桌面打开新会话")
    add_desktop_workflow_arguments(open_new_parser, require_id=False)
    open_new_prompt = open_new_parser.add_mutually_exclusive_group(required=True)
    open_new_prompt.add_argument("--prompt")
    open_new_prompt.add_argument("--prompt-stdin", action="store_true")
    open_new_parser.add_argument("--confirm-conversation", action="store_true")
    open_new_parser.set_defaults(func=desktop_conversation_command)

    switch_conversation_parser = conversation_subparsers.add_parser("switch", help="在桌面切换会话")
    add_desktop_workflow_arguments(switch_conversation_parser)
    switch_conversation_parser.add_argument("--target", required=True, help="目标会话标题")
    switch_conversation_parser.add_argument("--confirm-conversation", action="store_true")
    switch_conversation_parser.set_defaults(func=desktop_conversation_command)

    rename_conversation_parser = conversation_subparsers.add_parser("rename", help="重命名桌面会话")
    add_desktop_workflow_arguments(rename_conversation_parser)
    rename_conversation_parser.add_argument("--name", required=True)
    rename_conversation_parser.add_argument("--confirm-conversation", action="store_true")
    rename_conversation_parser.set_defaults(func=desktop_conversation_command)

    fork_conversation_parser = conversation_subparsers.add_parser("fork", help="分叉桌面会话")
    add_desktop_workflow_arguments(fork_conversation_parser)
    fork_conversation_parser.add_argument("--project-id")
    fork_conversation_parser.add_argument("--confirm-conversation", action="store_true")
    fork_conversation_parser.set_defaults(func=desktop_conversation_command)

    cancel_conversation_parser = conversation_subparsers.add_parser("cancel", help="取消桌面当前执行")
    add_desktop_workflow_arguments(cancel_conversation_parser)
    cancel_conversation_parser.add_argument("--confirm-conversation", action="store_true")
    cancel_conversation_parser.set_defaults(func=desktop_conversation_command)

    model_parser = subparsers.add_parser("model", help="查询 Antigravity CLI 模型")
    model_subparsers = model_parser.add_subparsers(dest="model_action", required=True)
    model_list_parser = model_subparsers.add_parser("list", help="通过 agy 列出可用模型与 slug")
    model_list_parser.set_defaults(func=model_command)
    model_desktop_list_parser = model_subparsers.add_parser("desktop-list", help="读取桌面模型菜单")
    add_desktop_workflow_arguments(model_desktop_list_parser)
    model_desktop_list_parser.add_argument("--trigger-name")
    model_desktop_list_parser.add_argument("--max-controls", type=nonnegative_integer, default=250)
    model_desktop_list_parser.set_defaults(
        func=lambda args: run_desktop_workflow(args, "model-list")
    )
    model_set_parser = model_subparsers.add_parser("set", help="切换桌面会话后续消息使用的模型")
    add_desktop_workflow_arguments(model_set_parser)
    model_set_parser.add_argument("--model", required=True)
    model_set_parser.add_argument("--trigger-name")
    model_set_parser.add_argument("--contains", action="store_true")
    model_set_parser.add_argument("--nth", type=nonnegative_integer)
    model_set_parser.add_argument("--confirm-model", action="store_true")
    model_set_parser.set_defaults(func=model_set_command)

    event_parser = subparsers.add_parser("event", help="接收、列出或等待 Antigravity Hook 事件")
    event_subparsers = event_parser.add_subparsers(dest="event_action", required=True)
    event_ingest_parser = event_subparsers.add_parser("ingest", help="从标准输入接收单个 Hook JSON")
    event_ingest_parser.add_argument("--kind", required=True, choices=("PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"))
    event_ingest_parser.set_defaults(func=event_command)
    event_list_parser = event_subparsers.add_parser("list", help="列出已净化的事件")
    event_list_parser.add_argument("--conversation-id")
    event_list_parser.add_argument("--limit", type=nonnegative_integer, default=100)
    event_list_parser.set_defaults(func=event_command)
    event_wait_parser = event_subparsers.add_parser("wait", help="等待会话 Stop/fullyIdle 事件")
    event_wait_parser.add_argument("--conversation-id", required=True)
    event_wait_parser.add_argument("--timeout-seconds", type=nonnegative_integer, default=30)
    event_wait_parser.set_defaults(func=event_command)
    event_wait_approval_parser = event_subparsers.add_parser(
        "wait-approval", help="阻塞等待 PreToolUse 命令审批请求"
    )
    event_wait_approval_parser.add_argument("--conversation-id", required=True)
    event_wait_approval_parser.add_argument("--tool-name")
    event_wait_approval_parser.add_argument("--after", help="仅接受此 ISO 8601 时间之后的事件")
    event_wait_approval_parser.add_argument(
        "--backend", choices=("auto", "sidecar", "local"), default="auto"
    )
    event_wait_approval_parser.add_argument(
        "--timeout-seconds", type=nonnegative_integer, default=300
    )
    event_wait_approval_parser.set_defaults(func=event_command)
    event_sync_parser = event_subparsers.add_parser("sync", help="从 Sidecar 导入并去重事件")
    event_sync_parser.add_argument("--conversation-id")
    event_sync_parser.set_defaults(func=event_command)

    task_parser = subparsers.add_parser("task", help="维护 Codex 与 Antigravity 任务映射")
    task_subparsers = task_parser.add_subparsers(dest="task_action", required=True)
    task_list_parser = task_subparsers.add_parser("list", help="列出任务映射")
    task_list_parser.set_defaults(func=task_command)
    task_link_parser = task_subparsers.add_parser("link", help="添加或更新任务映射")
    task_link_parser.add_argument("--conversation-id", required=True)
    task_link_parser.add_argument("--codex-task-id")
    task_link_parser.add_argument("--project-id")
    task_link_parser.add_argument("--title")
    task_link_parser.add_argument("--url")
    task_link_parser.add_argument("--model")
    task_link_parser.add_argument("--status")
    task_link_parser.set_defaults(func=task_command)
    task_remove_parser = task_subparsers.add_parser("remove", help="删除任务映射")
    task_remove_parser.add_argument("--conversation-id", required=True)
    task_remove_parser.set_defaults(func=task_command)

    artifact_parser = subparsers.add_parser("artifact", help="按 Hook 记录读取会话产物")
    artifact_subparsers = artifact_parser.add_subparsers(dest="artifact_action", required=True)
    artifact_list_parser = artifact_subparsers.add_parser("list", help="列出会话产物")
    artifact_list_parser.add_argument("--conversation-id", required=True)
    artifact_list_parser.add_argument("--limit", type=nonnegative_integer, default=200)
    artifact_list_parser.set_defaults(func=artifact_command)
    artifact_read_parser = artifact_subparsers.add_parser("read", help="读取 UTF-8 文本产物")
    artifact_read_parser.add_argument("--conversation-id", required=True)
    artifact_read_parser.add_argument("--path", required=True)
    artifact_read_parser.add_argument("--max-bytes", type=nonnegative_integer, default=1_048_576)
    artifact_read_parser.set_defaults(func=artifact_command)
    artifact_proceed_parser = artifact_subparsers.add_parser("proceed", help="批准桌面中的当前产物")
    add_desktop_workflow_arguments(artifact_proceed_parser)
    artifact_proceed_parser.add_argument("--confirm-artifact", action="store_true")
    artifact_proceed_parser.set_defaults(func=artifact_proceed_command)

    approval_parser = subparsers.add_parser("approval", help="检查或响应命令审批对话框")
    approval_subparsers = approval_parser.add_subparsers(
        dest="approval_action", required=True
    )
    approval_inspect_parser = approval_subparsers.add_parser(
        "inspect", help="只读检查当前审批对话框"
    )
    add_desktop_workflow_arguments(approval_inspect_parser)
    approval_inspect_parser.set_defaults(func=approval_command)
    approval_respond_parser = approval_subparsers.add_parser(
        "respond", help="按已观察到的精确按钮响应审批"
    )
    add_desktop_workflow_arguments(approval_respond_parser)
    approval_respond_parser.add_argument(
        "--decision", required=True, choices=("allow", "deny")
    )
    approval_respond_parser.add_argument("--button-name", required=True)
    approval_respond_parser.add_argument("--option-name", required=True)
    approval_respond_parser.add_argument("--tool-name", required=True)
    approval_respond_parser.add_argument("--event-observed-at", required=True)
    approval_respond_parser.add_argument("--confirm-approval", action="store_true")
    approval_respond_parser.set_defaults(func=approval_command)

    schedule_parser = subparsers.add_parser("schedule", help="管理 Companion Sidecar 定时任务")
    schedule_subparsers = schedule_parser.add_subparsers(dest="schedule_action", required=True)
    schedule_list_parser = schedule_subparsers.add_parser("list", help="列出定时任务")
    schedule_list_parser.set_defaults(func=schedule_command)
    schedule_create_parser = schedule_subparsers.add_parser("create", help="创建周期任务")
    schedule_prompt = schedule_create_parser.add_mutually_exclusive_group(required=True)
    schedule_prompt.add_argument("--prompt")
    schedule_prompt.add_argument("--prompt-stdin", action="store_true")
    schedule_create_parser.add_argument("--interval-seconds", required=True, type=nonnegative_integer)
    schedule_create_parser.add_argument("--conversation-id")
    schedule_create_parser.add_argument("--confirm-schedule", action="store_true")
    schedule_create_parser.set_defaults(func=schedule_command)
    schedule_remove_parser = schedule_subparsers.add_parser("remove", help="删除定时任务")
    schedule_remove_parser.add_argument("--schedule-id", required=True)
    schedule_remove_parser.add_argument("--confirm-schedule", action="store_true")
    schedule_remove_parser.set_defaults(func=schedule_command)
    schedule_open_parser = schedule_subparsers.add_parser("open", help="打开桌面计划任务页面")
    add_desktop_workflow_arguments(schedule_open_parser)
    schedule_open_parser.set_defaults(
        func=lambda args: run_desktop_workflow(args, "schedule-open")
    )

    project_parser = subparsers.add_parser("project", help="控制桌面项目入口")
    project_subparsers = project_parser.add_subparsers(dest="project_action", required=True)
    project_open_parser = project_subparsers.add_parser("open", help="打开指定项目")
    add_desktop_workflow_arguments(project_open_parser)
    project_open_parser.add_argument("--name", required=True)
    project_open_parser.add_argument("--confirm-project", action="store_true")
    project_open_parser.set_defaults(func=project_command)
    project_new_parser = project_subparsers.add_parser("new", help="打开新建项目向导")
    add_desktop_workflow_arguments(project_new_parser)
    project_new_parser.add_argument("--confirm-project", action="store_true")
    project_new_parser.set_defaults(func=project_command)

    settings_parser = subparsers.add_parser("settings", help="读取或修改桌面设置")
    settings_subparsers = settings_parser.add_subparsers(dest="settings_action", required=True)
    settings_open_parser = settings_subparsers.add_parser("open", help="打开设置页面")
    add_desktop_workflow_arguments(settings_open_parser)
    settings_open_parser.set_defaults(func=settings_command)
    settings_read_parser = settings_subparsers.add_parser("read", help="读取设置页可访问性快照")
    add_desktop_workflow_arguments(settings_read_parser)
    settings_read_parser.add_argument("--max-controls", type=nonnegative_integer, default=300)
    settings_read_parser.set_defaults(func=settings_command)
    settings_set_parser = settings_subparsers.add_parser("set", help="按标签修改已知设置控件")
    add_desktop_workflow_arguments(settings_set_parser)
    settings_set_parser.add_argument("--label", required=True)
    settings_set_parser.add_argument("--value", required=True)
    settings_set_parser.add_argument("--confirm-settings", action="store_true")
    settings_set_parser.set_defaults(func=settings_command)

    usage_parser = subparsers.add_parser("usage", help="读取桌面模型用量菜单")
    add_desktop_workflow_arguments(usage_parser)
    usage_parser.add_argument("--trigger-name")
    usage_parser.add_argument("--max-controls", type=nonnegative_integer, default=250)
    usage_parser.set_defaults(func=usage_command)

    activity_parser = subparsers.add_parser("activity", help="汇总会话、工具与子 Agent 活动")
    activity_parser.add_argument("--conversation-id", required=True)
    activity_parser.set_defaults(func=activity_command)

    review_parser = subparsers.add_parser("review", help="读取 Antigravity 会话归属的文件变更")
    review_subparsers = review_parser.add_subparsers(dest="review_action", required=True)
    review_changes_parser = review_subparsers.add_parser(
        "changes", help="打开 Review 页并返回当前会话的 diff 快照"
    )
    add_desktop_workflow_arguments(review_changes_parser)
    review_changes_parser.add_argument(
        "--max-controls", type=nonnegative_integer, default=300
    )
    review_changes_parser.set_defaults(func=review_command)

    companion_parser = subparsers.add_parser("companion", help="一次性全局注册或卸载 Antigravity Companion")
    companion_subparsers = companion_parser.add_subparsers(dest="companion_action", required=True)
    companion_status_parser = companion_subparsers.add_parser("status", help="检查全局 Companion 状态")
    companion_status_parser.set_defaults(func=companion_command)
    companion_install_parser = companion_subparsers.add_parser("install-global", help="全局安装并启用 Companion")
    companion_install_parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
        help=f"agentapi 默认项目 ID，默认 {DEFAULT_PROJECT_ID}",
    )
    companion_install_parser.add_argument("--confirm-install", action="store_true")
    companion_install_parser.set_defaults(func=companion_command)
    companion_uninstall_parser = companion_subparsers.add_parser("uninstall-global", help="全局卸载 Companion")
    companion_uninstall_parser.add_argument("--confirm-uninstall", action="store_true")
    companion_uninstall_parser.set_defaults(func=companion_command)

    setup_parser = subparsers.add_parser("setup", help="探测或装载 agy 与全局 Companion")
    setup_subparsers = setup_parser.add_subparsers(dest="setup_action", required=True)
    setup_status_parser = setup_subparsers.add_parser("status", help="只读检查完整能力状态")
    setup_status_parser.set_defaults(func=setup_command)
    setup_ensure_parser = setup_subparsers.add_parser(
        "ensure", help="使用官方安装器装载 agy 并全局注册 Companion"
    )
    setup_ensure_parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    setup_ensure_parser.add_argument("--agy-dir", type=Path)
    setup_ensure_parser.add_argument("--allow-system-drive", action="store_true")
    setup_ensure_parser.add_argument("--confirm-setup", action="store_true")
    setup_ensure_parser.set_defaults(func=setup_command)

    control_parser = subparsers.add_parser("control", help="通过 CDP 明确控制 Antigravity 页面")
    control_subparsers = control_parser.add_subparsers(dest="control_action", required=True)

    inspect_parser = control_subparsers.add_parser("inspect", help="只读检查指定会话页面状态")
    add_control_target_arguments(inspect_parser)
    inspect_parser.set_defaults(func=control_page)

    get_parser = control_subparsers.add_parser("get", help="只读获取唯一目标元素状态")
    add_control_target_arguments(get_parser, include_selector=True)
    get_parser.set_defaults(func=control_page)

    read_parser = control_subparsers.add_parser("read", help="只读获取页面或目标元素的完整可见文本")
    add_control_target_arguments(read_parser)
    read_parser.add_argument(
        "--selector",
        default=None,
        help="Playwright 定位器或 CSS 选择器；省略时只读取会话正文",
    )
    read_parser.add_argument(
        "--nth",
        type=nonnegative_integer,
        help="多匹配时显式选择从 0 开始的序号",
    )
    read_parser.set_defaults(func=control_page)

    snapshot_parser = control_subparsers.add_parser(
        "snapshot", help="只读获取页面可访问性快照和语义控件摘要"
    )
    add_control_target_arguments(snapshot_parser)
    snapshot_parser.add_argument(
        "--selector",
        default=None,
        help="快照根选择器；省略时只读取会话正文",
    )
    snapshot_parser.add_argument(
        "--nth",
        type=nonnegative_integer,
        help="多匹配时显式选择从 0 开始的序号",
    )
    snapshot_parser.add_argument(
        "--max-controls",
        type=nonnegative_integer,
        default=250,
        help="最多返回的控件摘要数，默认 250",
    )
    snapshot_parser.set_defaults(func=control_page)

    wait_parser = control_subparsers.add_parser("wait", help="只读等待目标元素状态")
    add_control_target_arguments(wait_parser, include_selector=True)
    wait_parser.add_argument(
        "--state",
        choices=("attached", "detached", "visible", "hidden"),
        default="visible",
        help="等待状态，默认 visible",
    )
    wait_parser.set_defaults(func=control_page)

    click_parser = control_subparsers.add_parser("click", help="点击唯一目标元素")
    add_control_target_arguments(click_parser, include_selector=True)
    add_mutating_control_arguments(click_parser)
    click_parser.set_defaults(func=control_page)

    fill_parser = control_subparsers.add_parser("fill", help="填充唯一目标元素")
    add_control_target_arguments(fill_parser, include_selector=True)
    add_mutating_control_arguments(fill_parser)
    fill_text = fill_parser.add_mutually_exclusive_group(required=True)
    fill_text.add_argument("--text", help="要填充的文本")
    fill_text.add_argument(
        "--text-stdin",
        action="store_true",
        help="从标准输入读取文本，适合多行或敏感内容",
    )
    fill_parser.set_defaults(func=control_page)

    press_parser = control_subparsers.add_parser("press", help="向唯一目标元素发送按键")
    add_control_target_arguments(press_parser, include_selector=True)
    add_mutating_control_arguments(press_parser)
    press_parser.add_argument("--key", required=True, help="Playwright 按键名，例如 Enter")
    press_parser.set_defaults(func=control_page)

    click_role_parser = control_subparsers.add_parser(
        "click-role", help="按可访问性角色和名称点击唯一目标"
    )
    add_role_target_arguments(click_role_parser)
    add_mutating_control_arguments(click_role_parser)
    click_role_parser.set_defaults(func=control_page)

    fill_role_parser = control_subparsers.add_parser(
        "fill-role", help="按可访问性角色和名称填充唯一目标"
    )
    add_role_target_arguments(fill_role_parser)
    add_mutating_control_arguments(fill_role_parser)
    fill_role_text = fill_role_parser.add_mutually_exclusive_group(required=True)
    fill_role_text.add_argument("--text", help="要填充的文本")
    fill_role_text.add_argument(
        "--text-stdin",
        action="store_true",
        help="从标准输入读取文本",
    )
    fill_role_parser.set_defaults(func=control_page)

    select_role_parser = control_subparsers.add_parser(
        "select-role", help="按可访问性角色和名称选择下拉选项"
    )
    add_role_target_arguments(select_role_parser)
    add_mutating_control_arguments(select_role_parser)
    select_role_parser.add_argument("--value", required=True, help="选项值或标签")
    select_role_parser.set_defaults(func=control_page)

    shortcut_parser = control_subparsers.add_parser(
        "shortcut", help="向页面发送已明确授权的快捷键"
    )
    add_control_target_arguments(shortcut_parser)
    add_mutating_control_arguments(shortcut_parser)
    shortcut_parser.add_argument("--key", required=True, help="Playwright 按键名，例如 Control+N")
    shortcut_parser.set_defaults(func=control_page)
    return parser


def main(argv=None):
    global LINKS_PATH
    configure_standard_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.store:
        LINKS_PATH = args.store.expanduser()
    try:
        args.func(args)
    except (
        BridgeError,
        CompanionError,
        RuntimeBridgeError,
        SetupError,
        StateError,
        OSError,
    ) as exc:
        parser.exit(1, f"错误: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
