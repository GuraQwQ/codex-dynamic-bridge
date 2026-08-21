import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def command_line(arguments):
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    import shlex

    return shlex.join(arguments)


def main():
    parser = argparse.ArgumentParser(description="安装 Antigravity 伴生插件文件。")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--endpoint-file", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source = Path(__file__).parent / "antigravity-plugin"
    destination = args.destination.expanduser().resolve()
    allowed_parents = {".agents", "_agents", "config"}
    if (
        destination.name != "codex-dynamic-bridge"
        or destination.parent.name != "plugins"
        or destination.parent.parent.name not in allowed_parents
    ):
        parser.error(
            "目标必须是 .agents/plugins/codex-dynamic-bridge、"
            "_agents/plugins/codex-dynamic-bridge 或 "
            "~/.gemini/config/plugins/codex-dynamic-bridge"
        )
    if destination.exists():
        if not args.force:
            parser.error(f"目标已存在，使用 --force 才能替换: {destination}")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data"),
    )

    sink = destination / "sidecars" / "codex-bridge" / "event_sink.py"
    endpoint = args.endpoint_file.expanduser().resolve()
    hooks = {"codex-dynamic-bridge-events": {}}
    for kind in ("PostToolUse", "PostInvocation", "Stop"):
        command = command_line(
            [sys.executable, str(sink), kind, "--endpoint-file", str(endpoint)]
        )
        entry = {"type": "command", "command": command, "timeout": 10}
        if kind == "PostToolUse":
            hooks["codex-dynamic-bridge-events"][kind] = [
                {"matcher": "*", "hooks": [entry]}
            ]
        else:
            hooks["codex-dynamic-bridge-events"][kind] = [entry]
    (destination / "hooks.json").write_text(
        json.dumps(hooks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"installed": str(destination), "endpointFile": str(endpoint)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
