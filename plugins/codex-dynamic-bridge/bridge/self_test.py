import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge import cli, companion, control, runtime, state


class FakeTarget:
    def __init__(self, text="目标", editable_error=False):
        self.text = text
        self.editable_error = editable_error
        self.value = ""
        self.clicked = False
        self.pressed = None
        self.waited = None

    def evaluate(self, _):
        if "element => ({tag:" in _:
            return {"tag": "input", "type": "checkbox"}
        return {
            "tag": "button",
            "id": "target",
            "role": "button",
            "ariaLabel": "测试目标",
            "inputType": None,
            "text": self.text,
            "valueLength": len(self.value or self.text),
        }

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def is_editable(self):
        if self.editable_error:
            raise RuntimeError("普通元素不可检查 editable")
        return True

    def inner_text(self, timeout):
        self.read_timeout = timeout
        return self.text

    def click(self, timeout):
        self.clicked = timeout

    def fill(self, text, timeout):
        self.value = text
        self.fill_timeout = timeout

    def press(self, key, timeout):
        self.pressed = (key, timeout)

    def select_option(self, value, timeout):
        self.value = value
        self.select_timeout = timeout

    def check(self, timeout):
        self.checked = True
        self.check_timeout = timeout

    def uncheck(self, timeout):
        self.checked = False
        self.check_timeout = timeout

    def aria_snapshot(self, timeout):
        self.snapshot_timeout = timeout
        return '- button "测试目标"'

    def locator(self, _):
        return FakeControlSummaryLocator()

    def wait_for(self, state, timeout):
        self.waited = (state, timeout)


class FakeLocator:
    def __init__(self, targets):
        self.targets = targets
        self.placeholder = FakeTarget()

    def count(self):
        return len(self.targets)

    def nth(self, index):
        if 0 <= index < len(self.targets):
            return self.targets[index]
        return self.placeholder


class FakeControlSummaryLocator:
    def evaluate_all(self, _, limit):
        return [
            {
                "index": 0,
                "role": "button",
                "name": "测试目标",
                "tag": "button",
                "type": None,
                "visible": True,
                "disabled": False,
            }
        ][:limit]


class FakeKeyboard:
    def __init__(self):
        self.key = None

    def press(self, key):
        self.key = key

    def type(self, text):
        self.typed = text


class FakePage:
    def __init__(self, targets):
        self.targets = targets
        self.settled = None
        self.keyboard = FakeKeyboard()

    def locator(self, _):
        return FakeLocator(self.targets)

    def get_by_role(self, role, name, exact):
        self.role_query = (role, name, exact)
        return FakeLocator(self.targets)

    def get_by_text(self, text, exact):
        self.text_query = (text, exact)
        return FakeLocator(self.targets)

    def get_by_label(self, label, exact):
        self.label_query = (label, exact)
        return FakeLocator(self.targets)

    def evaluate(self, _):
        return {
            "title": "测试会话",
            "url": "https://127.0.0.1:3900/c/test",
            "hasFocus": True,
            "readyState": "complete",
            "viewport": {"width": 1000, "height": 700},
            "activeElement": None,
            "counts": {"buttons": 1, "inputs": 0, "textareas": 0, "contentEditables": 0},
        }

    def wait_for_timeout(self, milliseconds):
        self.settled = milliseconds


class FakeProcessResult:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.previous_links_path = cli.LINKS_PATH
        cli.LINKS_PATH = Path(self.temporary_directory.name) / "links.json"

    def tearDown(self):
        cli.LINKS_PATH = self.previous_links_path
        self.temporary_directory.cleanup()

    def capture(self, function, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(args)
        return result, json.loads(output.getvalue())

    def add(self, **overrides):
        values = {
            "id": "task-1",
            "title": "任务一",
            "url": "https://example.com/1",
            "source": "Antigravity",
            "updatedAt": "2026-08-21T08:00:00Z",
        }
        values.update(overrides)
        return self.capture(cli.add_link, argparse.Namespace(**values))[0]

    def test_add_只接受较新的记录(self):
        self.add()
        result = self.add(title="旧标题", updatedAt="2026-08-21T07:00:00Z")
        self.assertFalse(result["changed"])
        self.assertEqual(cli.load_links()[0]["title"], "任务一")

    def test_add_正确比较不同时区(self):
        self.add(updatedAt="2026-08-21T08:00:00+08:00")
        self.add(title="较新", updatedAt="2026-08-21T01:00:00Z")
        link = cli.load_links()[0]
        self.assertEqual(link["title"], "较新")
        self.assertEqual(link["updatedAt"], "2026-08-21T01:00:00Z")

    def test_sync_只持久化允许的元数据字段(self):
        source = Path(self.temporary_directory.name) / "source.json"
        source.write_text(
            json.dumps(
                [
                    {
                        "id": "task-2",
                        "title": "任务二",
                        "url": "codex://tasks/task-2",
                        "source": "Codex",
                        "updatedAt": "2026-08-21T09:00:00Z",
                        "messageBody": "不应保存",
                        "token": "不应保存",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result, _ = self.capture(cli.sync_links, argparse.Namespace(source=str(source)))
        self.assertEqual(result, {"read": 1, "changed": 1, "total": 1})
        self.assertEqual(set(cli.load_links()[0]), set(cli.REQUIRED_FIELDS))

    def test_损坏的存储不会被覆盖(self):
        cli.LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        cli.LINKS_PATH.write_text("{broken", encoding="utf-8")
        with self.assertRaises(cli.BridgeError):
            self.add()
        self.assertEqual(cli.LINKS_PATH.read_text(encoding="utf-8"), "{broken")

    def test_无效时间戳与协议会被拒绝(self):
        with self.assertRaises(cli.BridgeError):
            self.add(updatedAt="2026-08-21T08:00:00")
        with self.assertRaises(cli.BridgeError):
            self.add(url="file:///secret.txt")

    def test_save_使用完整_json_且不残留临时文件(self):
        self.add()
        self.assertEqual(len(json.loads(cli.LINKS_PATH.read_text(encoding="utf-8"))), 1)
        self.assertEqual(list(cli.LINKS_PATH.parent.glob("*.tmp")), [])

    def test_discover_只返回会话路由(self):
        pages = [
            {
                "type": "page",
                "id": "devtools-1",
                "title": "会话一",
                "url": "https://127.0.0.1:3900/c/conversation-1?section=x",
            },
            {"type": "page", "id": "loader", "title": "加载", "url": "data:text/html,x"},
            {"type": "worker", "id": "worker", "title": "", "url": ""},
        ]
        self.assertEqual(
            cli.discover_sessions(pages),
            [
                {
                    "conversationId": "conversation-1",
                    "devtoolsId": "devtools-1",
                    "title": "会话一",
                    "url": "https://127.0.0.1:3900/c/conversation-1?section=x",
                }
            ],
        )

    def test_多会话时必须显式选择(self):
        sessions = [
            {"conversationId": "a", "devtoolsId": "1"},
            {"conversationId": "b", "devtoolsId": "2"},
        ]
        with self.assertRaises(cli.BridgeError):
            cli.select_sessions(sessions)
        self.assertEqual(cli.select_sessions(sessions, conversation_id="2"), [sessions[1]])
        self.assertEqual(cli.select_sessions(sessions, all_sessions=True), sessions)

    def test_remove_删除存在的链接并拒绝未知_id(self):
        self.add()
        result, _ = self.capture(cli.remove_link, argparse.Namespace(id="task-1"))
        self.assertEqual(result, {"removed": "task-1", "total": 0})
        with self.assertRaises(cli.BridgeError):
            cli.remove_link(argparse.Namespace(id="missing"))

    def test_control_inspect_只读返回页面状态(self):
        result = control.perform_action(
            FakePage([FakeTarget()]),
            argparse.Namespace(control_action="inspect"),
        )
        self.assertEqual(result["action"], "inspect")
        self.assertTrue(result["page"]["hasFocus"])

    def test_control_只匹配回环地址的会话页面(self):
        self.assertEqual(
            control.conversation_id_from_url("https://127.0.0.1:3900/c/test"),
            "test",
        )
        self.assertIsNone(
            control.conversation_id_from_url("https://example.com/c/test")
        )

    def test_control_get_默认要求选择器唯一(self):
        page = FakePage([FakeTarget("一"), FakeTarget("二")])
        args = argparse.Namespace(control_action="get", selector="button", nth=None)
        with self.assertRaises(control.ControlError):
            control.perform_action(page, args)
        args.nth = 1
        result = control.perform_action(page, args)
        self.assertEqual(result["target"]["text"], "二")

    def test_control_get_普通元素报告不可编辑(self):
        page = FakePage([FakeTarget("标题", editable_error=True)])
        result = control.perform_action(
            page,
            argparse.Namespace(control_action="get", selector="title", nth=None),
        )
        self.assertFalse(result["target"]["editable"])

    def test_control_read_返回完整可见文本(self):
        text = "会话正文" * 100
        target = FakeTarget(text)
        result = control.perform_action(
            FakePage([target]),
            argparse.Namespace(
                control_action="read",
                selector="body",
                nth=None,
                timeout_ms=1234,
            ),
        )
        self.assertEqual(result["content"], {"text": text, "textLength": len(text)})
        self.assertEqual(target.read_timeout, 1234)

    def test_control_read_默认读取_body(self):
        args = cli.build_parser().parse_args(
            ["control", "read", "--id", "conversation-1"]
        )
        self.assertEqual(args.selector, "body")
        self.assertIsNone(args.nth)
        self.assertFalse(hasattr(args, "confirm_control"))

    def test_control_snapshot_返回可访问性信息(self):
        target = FakeTarget()
        result = control.perform_action(
            FakePage([target]),
            argparse.Namespace(
                control_action="snapshot",
                selector="body",
                nth=None,
                timeout_ms=900,
                max_controls=10,
            ),
        )
        self.assertEqual(result["snapshot"]["controlCount"], 1)
        self.assertIn("测试目标", result["snapshot"]["aria"])
        self.assertEqual(target.snapshot_timeout, 900)

    def test_control_语义动作与快捷键(self):
        target = FakeTarget()
        page = FakePage([target])
        common = {
            "role": "button",
            "name": "新建对话",
            "contains": False,
            "nth": None,
            "timeout_ms": 1000,
            "settle_ms": 10,
        }
        control.perform_action(
            page,
            argparse.Namespace(control_action="click-role", **common),
        )
        self.assertEqual(page.role_query, ("button", "新建对话", True))
        self.assertEqual(target.clicked, 1000)

        control.perform_action(
            page,
            argparse.Namespace(
                control_action="select-role",
                value="gemini",
                **common,
            ),
        )
        self.assertEqual(target.value, "gemini")

        result = control.perform_action(
            page,
            argparse.Namespace(
                control_action="shortcut",
                key="Control+N",
                settle_ms=10,
            ),
        )
        self.assertEqual(page.keyboard.key, "Control+N")
        self.assertTrue(result["completed"])

    def test_desktop_会话命令与切换(self):
        target = FakeTarget()
        page = FakePage([target])
        common = {"timeout_ms": 1000, "settle_ms": 10}
        control.perform_workflow(
            page,
            argparse.Namespace(
                workflow_action="conversation-rename",
                name="新标题",
                **common,
            ),
        )
        self.assertEqual(target.value, "/rename 新标题")
        self.assertEqual(target.pressed, ("Enter", 1000))

        control.perform_workflow(
            page,
            argparse.Namespace(
                workflow_action="conversation-switch",
                target="目标会话",
                **common,
            ),
        )
        self.assertEqual(page.keyboard.typed, "目标会话")
        self.assertEqual(page.keyboard.key, "Enter")

    def test_desktop_设置布尔值(self):
        target = FakeTarget()
        page = FakePage([target])
        result = control.perform_workflow(
            page,
            argparse.Namespace(
                workflow_action="settings-set",
                label="Enable Telemetry",
                value="false",
                timeout_ms=1000,
                settle_ms=10,
            ),
        )
        self.assertEqual(page.label_query, ("Enable Telemetry", True))
        self.assertFalse(target.checked)
        self.assertEqual(result["detail"]["value"], "false")

    def test_desktop_模型切换(self):
        target = FakeTarget("Gemini Current")
        page = FakePage([target])
        result = control.perform_workflow(
            page,
            argparse.Namespace(
                workflow_action="model-set",
                model="Claude Target",
                trigger_name=None,
                contains=False,
                nth=None,
                timeout_ms=1000,
                settle_ms=10,
            ),
        )
        self.assertTrue(result["detail"]["changed"])
        self.assertEqual(page.text_query, ("Claude Target", True))

    def test_control_写动作执行并返回后置状态(self):
        target = FakeTarget()
        page = FakePage([target])
        common = {
            "selector": "#target",
            "nth": None,
            "timeout_ms": 1234,
            "settle_ms": 25,
        }

        click_result = control.perform_action(
            page,
            argparse.Namespace(control_action="click", **common),
        )
        self.assertEqual(target.clicked, 1234)
        self.assertTrue(click_result["completed"])

        fill_result = control.perform_action(
            page,
            argparse.Namespace(control_action="fill", text="新文本", **common),
        )
        self.assertEqual(target.value, "新文本")
        self.assertEqual(fill_result["targetAfter"]["valueLength"], 3)

        control.perform_action(
            page,
            argparse.Namespace(control_action="press", key="Enter", **common),
        )
        self.assertEqual(target.pressed, ("Enter", 1234))
        self.assertEqual(page.settled, 25)

    def test_control_wait_验证等待后的唯一性(self):
        target = FakeTarget()
        result = control.perform_action(
            FakePage([target]),
            argparse.Namespace(
                control_action="wait",
                selector="#target",
                nth=None,
                state="visible",
                timeout_ms=500,
            ),
        )
        self.assertEqual(target.waited, ("visible", 500))
        self.assertEqual(result["matchCount"], 1)

    def test_control_写动作必须显式确认(self):
        with self.assertRaises(cli.BridgeError):
            cli.control_page(
                argparse.Namespace(control_action="click", confirm_control=False)
            )

    def test_agy_按项目模型和会话运行(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return FakeProcessResult(
                json.dumps(
                    {
                        "conversation_id": "conversation-1",
                        "status": "SUCCESS",
                        "response": "完成",
                    }
                )
            )

        client = runtime.AgyClient(executable="F:/tools/agy.exe", runner=runner)
        result = client.run_prompt(
            "继续",
            conversation_id="conversation-1",
            project_id="project-1",
            model="gemini-test",
            effort="high",
            timeout_seconds=20,
        )
        self.assertEqual(result["response"], "完成")
        command = calls[0][0]
        self.assertIn("--conversation=conversation-1", command)
        self.assertIn("--project=project-1", command)
        self.assertIn("gemini-test", command)

    def test_agy_拒绝失败和非_json(self):
        client = runtime.AgyClient(
            executable="agy",
            runner=lambda *args, **kwargs: FakeProcessResult("not-json"),
        )
        with self.assertRaises(runtime.RuntimeBridgeError):
            client.run_prompt("测试")

        client = runtime.AgyClient(
            executable="agy",
            runner=lambda *args, **kwargs: FakeProcessResult("", returncode=2, stderr="失败"),
        )
        with self.assertRaises(runtime.RuntimeBridgeError):
            client.list_models()

    def test_需要模型参数时禁止_sidecar_降级(self):
        with self.assertRaises(cli.BridgeError):
            cli.select_runtime_backend("sidecar", require_agy=True)

    def test_event_只保存白名单并同步任务(self):
        event_store = state.EventStore(Path(self.temporary_directory.name) / "events.jsonl")
        task_store = state.TaskStore(Path(self.temporary_directory.name) / "tasks.json")
        event = event_store.append(
            "Stop",
            {
                "conversationId": "conversation-1",
                "modelName": "gemini-test",
                "fullyIdle": True,
                "artifactDirectoryPath": str(Path(self.temporary_directory.name) / "artifacts"),
                "token": "不应保存",
            },
        )
        self.assertNotIn("token", event)
        task = task_store.sync_event(event)
        self.assertEqual(task["status"], "idle")
        self.assertEqual(task["model"], "gemini-test")

    def test_event_wait_读取已完成事件(self):
        event_store = state.EventStore(Path(self.temporary_directory.name) / "events.jsonl")
        event_store.append(
            "Stop",
            {"conversationId": "conversation-1", "fullyIdle": True},
        )
        result = event_store.wait("conversation-1", timeout_seconds=0)
        self.assertTrue(result["fullyIdle"])

    def test_companion_全局安装幂等且保留现有配置(self):
        home = Path(self.temporary_directory.name) / "home"
        root = home / ".gemini" / "config"
        root.mkdir(parents=True)
        config_path = root / "config.json"
        state.atomic_write_json(
            config_path,
            {
                "theme": "dark",
                "sidecars": {"other/worker": {"enabled": False}},
            },
        )
        env = {"USERPROFILE": str(home)}

        first = companion.install_global("project-1", env)
        destination = Path(first["destination"])
        self.assertTrue(first["installed"])
        self.assertTrue(first["enabled"])
        self.assertFalse(first["restartRequired"])
        self.assertFalse(any(destination.rglob("__pycache__")))
        hooks = json.loads((destination / "hooks.json").read_text(encoding="utf-8"))
        events = hooks["codex-dynamic-bridge-events"]
        self.assertIn("hooks", events["PostToolUse"][0])
        self.assertIn("command", events["PostInvocation"][0])
        self.assertIn("command", events["Stop"][0])

        (destination / "stale.txt").write_text("旧文件", encoding="utf-8")
        second = companion.install_global("project-2", env)
        self.assertFalse((destination / "stale.txt").exists())
        self.assertEqual(second["projectId"], "project-2")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["theme"], "dark")
        self.assertEqual(config["sidecars"]["other/worker"], {"enabled": False})

    def test_companion_卸载只删除自己的配置和目录(self):
        home = Path(self.temporary_directory.name) / "home"
        env = {"USERPROFILE": str(home)}
        companion.install_global("project-1", env)
        config_path = home / ".gemini" / "config" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["sidecars"]["other/worker"] = {"enabled": True}
        config["keep"] = 1
        state.atomic_write_json(config_path, config)

        result = companion.uninstall_global(env)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(result["removed"])
        self.assertFalse(result["installed"])
        self.assertNotIn(companion.SIDECAR_ID, config["sidecars"])
        self.assertEqual(config["sidecars"]["other/worker"], {"enabled": True})
        self.assertEqual(config["keep"], 1)

    def test_companion_无效配置和未知目录均零修改(self):
        home = Path(self.temporary_directory.name) / "home"
        env = {"USERPROFILE": str(home)}
        root = home / ".gemini" / "config"
        root.mkdir(parents=True)
        config_path = root / "config.json"
        config_path.write_text("{invalid", encoding="utf-8")
        destination = companion.destination_plugin(env)
        with self.assertRaises(companion.CompanionError):
            companion.install_global("project-1", env)
        self.assertFalse(destination.exists())
        self.assertEqual(config_path.read_text(encoding="utf-8"), "{invalid")

        config_path.write_text("{}\n", encoding="utf-8")
        destination.mkdir(parents=True)
        (destination / "unknown.txt").write_text("保留", encoding="utf-8")
        with self.assertRaises(companion.CompanionError):
            companion.install_global("project-1", env)
        self.assertEqual((destination / "unknown.txt").read_text(encoding="utf-8"), "保留")
        self.assertEqual(config_path.read_text(encoding="utf-8"), "{}\n")

    def test_companion_endpoint_只接受回环地址并执行鉴权健康检查(self):
        home = Path(self.temporary_directory.name) / "home"
        env = {"USERPROFILE": str(home)}
        endpoint_path = companion.endpoint_file(env)
        self.assertEqual(endpoint_path, runtime.default_sidecar_endpoint_file(env))
        endpoint_path.parent.mkdir(parents=True)
        state.atomic_write_json(
            endpoint_path,
            {"url": "http://127.0.0.1:12345", "token": "test-token"},
        )
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.object(companion, "urlopen", return_value=response) as open_url:
            self.assertTrue(companion.endpoint_ready(env))
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:12345/v1/health")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

        state.atomic_write_json(
            endpoint_path,
            {"url": "https://example.com", "token": "test-token"},
        )
        with mock.patch.object(companion, "urlopen") as open_url:
            self.assertFalse(companion.endpoint_ready(env))
            open_url.assert_not_called()

    def test_companion_配置写入失败时恢复旧安装(self):
        home = Path(self.temporary_directory.name) / "home"
        env = {"USERPROFILE": str(home)}
        first = companion.install_global("project-1", env)
        destination = Path(first["destination"])
        (destination / "sentinel.txt").write_text("旧安装", encoding="utf-8")
        config_path = Path(first["configPath"])
        before = config_path.read_text(encoding="utf-8")
        real_atomic_write = companion.atomic_write_json

        def fail_config_write(path, value):
            if Path(path) == config_path:
                raise OSError("模拟配置写入失败")
            return real_atomic_write(path, value)

        with mock.patch.object(companion, "atomic_write_json", side_effect=fail_config_write):
            with self.assertRaises(OSError):
                companion.install_global("project-2", env)

        self.assertEqual((destination / "sentinel.txt").read_text(encoding="utf-8"), "旧安装")
        self.assertEqual(config_path.read_text(encoding="utf-8"), before)

    def test_companion_旧目录改名失败时不删除旧安装(self):
        home = Path(self.temporary_directory.name) / "home"
        env = {"USERPROFILE": str(home)}
        first = companion.install_global("project-1", env)
        destination = Path(first["destination"])
        (destination / "sentinel.txt").write_text("旧安装", encoding="utf-8")
        stage = destination.parent / ".prepared-stage"
        stage.mkdir()
        (stage / "plugin.json").write_text(
            '{"name":"codex-dynamic-bridge"}\n', encoding="utf-8"
        )

        with (
            mock.patch.object(companion, "staged_plugin", return_value=stage),
            mock.patch.object(companion.os, "replace", side_effect=OSError("模拟改名失败")),
        ):
            with self.assertRaises(OSError):
                companion.install_global("project-2", env)

        self.assertEqual((destination / "sentinel.txt").read_text(encoding="utf-8"), "旧安装")

    def test_companion_cli_写操作必须显式确认(self):
        with self.assertRaises(cli.BridgeError):
            cli.companion_command(
                argparse.Namespace(
                    companion_action="install-global",
                    confirm_install=False,
                    project_id="project-1",
                )
            )
        with self.assertRaises(cli.BridgeError):
            cli.companion_command(
                argparse.Namespace(
                    companion_action="uninstall-global",
                    confirm_uninstall=False,
                )
            )

        args = cli.build_parser().parse_args(
            ["companion", "install-global", "--confirm-install"]
        )
        self.assertEqual(args.project_id, companion.DEFAULT_PROJECT_ID)

    def test_doctor_自动报告_companion_注册状态(self):
        summary = {"sidecar": {"configured": False}}
        registration = {"installed": True, "antigravityRunning": True}
        with (
            mock.patch.object(cli, "runtime_summary", return_value=summary),
            mock.patch.object(cli, "companion_status", return_value=registration),
            mock.patch.object(cli, "read_antigravity_port", side_effect=cli.BridgeError("无端口")),
        ):
            result, output = self.capture(cli.doctor_command, None)
        self.assertEqual(result["companion"], registration)
        self.assertEqual(output["companion"], registration)

    def test_artifact_限制目录和文本类型(self):
        root = Path(self.temporary_directory.name) / "artifacts"
        root.mkdir()
        (root / "plan.md").write_text("计划", encoding="utf-8")
        (root / "token.txt").write_text("敏感", encoding="utf-8")
        (root / "image.png").write_bytes(b"png")
        items = state.list_artifacts(root)
        self.assertEqual(items, [{"path": "image.png", "size": 3}, {"path": "plan.md", "size": 6}])
        self.assertEqual(state.read_artifact(root, "plan.md"), "计划")
        with self.assertRaises(state.StateError):
            state.read_artifact(root, "image.png")
        with self.assertRaises(state.StateError):
            state.read_artifact(root, "../outside.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
