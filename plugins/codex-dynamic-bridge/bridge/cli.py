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


REQUIRED_FIELDS = ("id", "title", "url", "source", "updatedAt")
ALLOWED_URL_SCHEMES = {"http", "https", "codex"}


class BridgeError(RuntimeError):
    """可直接展示给用户的桥接错误。"""


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


def discover_links(_):
    sessions = discover_sessions()
    print(json.dumps(sessions, ensure_ascii=False, indent=2))
    return sessions


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
    if args.control_action == "fill" and args.text_stdin:
        args.text = sys.stdin.read()

    session = select_sessions(discover_sessions(), args.id)[0]
    try:
        result = execute_control(read_antigravity_port(), session["conversationId"], args)
    except ControlError as exc:
        raise BridgeError(str(exc)) from exc
    result["conversationId"] = session["conversationId"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


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

    live_parser = subparsers.add_parser("live", help="读取并保存 Antigravity 会话元数据")
    selection = live_parser.add_mutually_exclusive_group()
    selection.add_argument("--id", help="会话 conversationId 或 DevTools id")
    selection.add_argument("--all", action="store_true", help="保存所有发现的会话")
    live_parser.set_defaults(func=live_link)

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
        default="body",
        help="Playwright 定位器或 CSS 选择器，默认 body",
    )
    read_parser.add_argument(
        "--nth",
        type=nonnegative_integer,
        help="多匹配时显式选择从 0 开始的序号",
    )
    read_parser.set_defaults(func=control_page)

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
    return parser


def main(argv=None):
    global LINKS_PATH
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.store:
        LINKS_PATH = args.store.expanduser()
    try:
        args.func(args)
    except (BridgeError, OSError) as exc:
        parser.exit(1, f"错误: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
