import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind")
    parser.add_argument("--endpoint-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        endpoint = json.loads(args.endpoint_file.read_text(encoding="utf-8"))
        payload = json.load(sys.stdin)
        payload["kind"] = args.kind
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
    print("{}")


if __name__ == "__main__":
    main()
