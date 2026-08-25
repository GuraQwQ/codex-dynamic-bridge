import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen


# Antigravity 的 Hook 协议固定使用 UTF-8；Windows 管道默认可能是 GBK。
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


EVENT_FIELDS = {
    "conversationId",
    "workspacePaths",
    "artifactDirectoryPath",
    "modelName",
    "terminationReason",
    "fullyIdle",
    "error",
    "stepIdx",
    "projectId",
    "status",
}


def sanitize_event(kind, payload):
    event = {key: payload[key] for key in EVENT_FIELDS if key in payload}
    event["kind"] = kind
    tool_call = payload.get("toolCall")
    tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
    if isinstance(tool_name, str) and tool_name.strip():
        event["toolName"] = tool_name.strip()[:128]
    if kind == "PreToolUse":
        event["approvalState"] = "requested"
        event["status"] = "waiting_approval"
    return event


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind")
    parser.add_argument("--endpoint-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        endpoint = json.loads(args.endpoint_file.read_text(encoding="utf-8"))
        payload = json.load(sys.stdin)
        body = json.dumps(
            sanitize_event(args.kind, payload), ensure_ascii=False
        ).encode("utf-8")
        request = Request(
            endpoint["url"].rstrip("/") + "/v1/events",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {endpoint['token']}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=10) as response:
            response.read()
    except Exception as exc:
        print(f"Codex Dynamic Bridge 事件上报失败: {exc}", file=sys.stderr)
    if args.kind == "PreToolUse":
        print(
            json.dumps(
                {
                    "decision": "ask",
                    "reason": "Codex Dynamic Bridge 已记录此权限请求，请确认后再允许。",
                },
                ensure_ascii=False,
            )
        )
    else:
        print("{}")


if __name__ == "__main__":
    main()
