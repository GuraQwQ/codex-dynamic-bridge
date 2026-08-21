import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge import cli, companion, control, runtime, setup as bridge_setup, state


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

    def get_attribute(self, name):
        return getattr(self, name.replace("-", "_"), None)


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
    def __init__(
        self,
        targets,
        url="https://127.0.0.1:3900/c/test",
        role_targets=None,
    ):
        self.targets = targets
        self.role_targets = role_targets or {}
        self.settled = None
        self.keyboard = FakeKeyboard()
        self.url = url
        self.next_url = "https://127.0.0.1:3900/c/new-conversation"
        self.url_sequence = []

    def locator(self, _):
        return FakeLocator(self.targets)

    def get_by_role(self, role, name, exact):
        self.role_query = (role, name, exact)
        return FakeLocator(self.role_targets.get((role, name), self.targets))

    def get_by_text(self, text, exact):
        self.text_query = (text, exact)
        return FakeLocator(self.targets)

    def get_by_label(self, label, exact):
        self.label_query = (label, exact)
        return FakeLocator(self.targets)

    def evaluate(self, _):
        return {
            "title": "测试会话",
            "url": self.url,
            "hasFocus": True,
            "readyState": "complete",
            "viewport": {"width": 1000, "height": 700},
            "activeElement": None,
            "counts": {"buttons": 1, "inputs": 0, "textareas": 0, "contentEditables": 0},
        }

    def wait_for_timeout(self, milliseconds):
        self.settled = milliseconds

    def wait_for_url(self, predicate, timeout):
        self.wait_url_timeout = timeout
        self.url = self.url_sequence.pop(0) if self.url_sequence else self.next_url
        if not predicate(self.url):
            raise RuntimeError("URL 未匹配")


class FakeHTTPResponse:
    def __init__(self, content):
        self.content = content
        self.offset = 0
        self.status = 200
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        if self.offset >= len(self.content):
            return b""
        end = len(self.content) if size < 0 else self.offset + size
        chunk = self.content[self.offset:end]
        self.offset += len(chunk)
        return chunk


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

    def test_discover_pages_保留可信外壳并拒绝外部页面(self):
        pages = [
            {
                "id": "shell-1",
                "type": "page",
                "title": "Antigravity",
                "url": "https://127.0.0.1:3639/?section=test",
            },
            {
                "id": "conversation-1",
                "type": "page",
                "title": "会话",
                "url": "https://localhost:3639/c/conversation-1",
            },
            {
                "id": "external",
                "type": "page",
                "title": "Antigravity",
                "url": "https://example.com/",
            },
            {
                "id": "wrong-title",
                "type": "page",
                "title": "其他页面",
                "url": "https://127.0.0.1:3639/",
            },
        ]
        discovered = cli.discover_app_pages(pages)
        self.assertEqual(
            [(item["kind"], item["devtoolsId"]) for item in discovered],
            [("shell", "shell-1"), ("conversation", "conversation-1")],
        )

    def test_select_app_page_多目标时拒绝猜测并支持_devtools_id(self):
        pages = [
            {"devtoolsId": "shell-1", "conversationId": None},
            {"devtoolsId": "conversation-1", "conversationId": "conversation-id"},
        ]
        with self.assertRaises(cli.BridgeError):
            cli.select_app_page(pages)
        self.assertEqual(cli.select_app_page(pages, "shell-1"), pages[0])
        self.assertEqual(cli.select_app_page(pages, "conversation-id"), pages[1])

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

    def test_desktop_根页面可_bootstrap_新会话(self):
        new_button = FakeTarget()
        message_input = FakeTarget()
        send_button = FakeTarget()
        page = FakePage(
            [],
            url="https://127.0.0.1:3900/c/old-conversation",
            role_targets={
                ("button", "New Conversation"): [new_button],
                ("combobox", "Message input"): [message_input],
                ("button", "Send message"): [send_button],
            },
        )
        page.url_sequence = [
            "https://127.0.0.1:3900/?section=test",
            "https://127.0.0.1:3900/c/new-conversation",
        ]
        result = control.perform_workflow(
            page,
            argparse.Namespace(
                workflow_action="conversation-new",
                prompt="执行新任务",
                timeout_ms=1200,
                settle_ms=10,
            ),
        )
        self.assertEqual(new_button.clicked, 1200)
        self.assertEqual(message_input.value, "执行新任务")
        self.assertEqual(send_button.clicked, 1200)
        self.assertEqual(page.wait_url_timeout, 1200)
        self.assertEqual(result["detail"]["conversationId"], "new-conversation")
        self.assertEqual(result["detail"]["promptLength"], 5)

    def test_open_new_无需会话_id_并传递可信页面_url(self):
        target = {
            "kind": "shell",
            "conversationId": None,
            "devtoolsId": "shell-1",
            "title": "Antigravity",
            "url": "https://127.0.0.1:3900/?section=test",
        }
        args = argparse.Namespace(
            id=None,
            prompt="执行任务",
            prompt_stdin=False,
            timeout_ms=1000,
            settle_ms=10,
        )
        with (
            mock.patch.object(cli, "discover_app_pages", return_value=[target]),
            mock.patch.object(cli, "read_antigravity_port", return_value=3000),
            mock.patch.object(
                control,
                "execute_control",
                return_value={"detail": {"conversationId": "created-id"}},
            ) as execute,
        ):
            result, output = self.capture(cli.run_new_conversation_workflow, args)
        self.assertEqual(result["conversationId"], "created-id")
        self.assertTrue(result["bootstrappedFromShell"])
        self.assertEqual(output, result)
        self.assertEqual(execute.call_args.kwargs["page_url"], target["url"])

        parsed = cli.build_parser().parse_args(
            [
                "conversation",
                "open-new",
                "--prompt",
                "执行任务",
                "--confirm-conversation",
            ]
        )
        self.assertIsNone(parsed.id)

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

    def test_setup_从_codex_home_发现_agy(self):
        codex_home = Path(self.temporary_directory.name) / "codex"
        executable = codex_home / "tools" / "agy" / "agy.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"agy")
        self.assertEqual(
            runtime.find_agy({"CODEX_HOME": str(codex_home)}),
            str(executable),
        )

    def test_setup_系统盘默认拒绝且官方清单校验后原子安装(self):
        install_dir = Path(self.temporary_directory.name) / "agy"
        with self.assertRaises(bridge_setup.SetupError):
            bridge_setup.validate_install_dir(
                install_dir,
                env={"SystemDrive": install_dir.drive},
                platform_name="nt",
            )

        payload = b"MZtest-agy"
        digest = hashlib.sha512(payload).hexdigest()
        manifest = json.dumps(
            {
                "version": "1.2.3",
                "url": "https://storage.googleapis.com/example/agy.exe",
                "sha512": digest,
            }
        ).encode("utf-8")
        opened = []

        def opener(request, **_kwargs):
            opened.append(request.full_url)
            content = manifest if "/manifests/" in request.full_url else payload
            return FakeHTTPResponse(content)

        result = bridge_setup.ensure_agy(
            install_dir=install_dir,
            env={"PATH": "", "SystemDrive": install_dir.drive},
            allow_system_drive=True,
            platform_name="nt",
            machine_name="AMD64",
            opener=opener,
            prefer_curl=False,
        )
        self.assertTrue(result["installed"])
        self.assertEqual((install_dir / "agy.exe").read_bytes(), payload)
        self.assertEqual(result["sha512"], digest)
        self.assertEqual(len(opened), 2)
        self.assertIn("windows_amd64.json", opened[0])

    def test_setup_校验失败时不写入_agy(self):
        install_dir = Path(self.temporary_directory.name) / "agy"
        manifest = json.dumps(
            {
                "version": "1.2.3",
                "url": "https://storage.googleapis.com/example/agy.exe",
                "sha512": "0" * 128,
            }
        ).encode("utf-8")

        def opener(request, **_kwargs):
            content = manifest if "/manifests/" in request.full_url else b"MZcorrupt"
            return FakeHTTPResponse(content)

        with self.assertRaises(bridge_setup.SetupError):
            bridge_setup.ensure_agy(
                install_dir=install_dir,
                env={"SystemDrive": install_dir.drive},
                allow_system_drive=True,
                platform_name="nt",
                machine_name="AMD64",
                opener=opener,
                prefer_curl=False,
            )
        self.assertFalse((install_dir / "agy.exe").exists())

    def test_setup_curl_从_partial_续传后仍校验_sha512(self):
        payload = b"MZ-complete-binary"
        staging = Path(self.temporary_directory.name) / "agy.partial"
        staging.write_bytes(payload[:5])
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            output = Path(command[command.index("--output") + 1])
            with output.open("ab") as stream:
                stream.write(payload[5:])
            return FakeProcessResult("")

        with mock.patch.object(
            bridge_setup.shutil, "which", return_value="F:/Windows/curl.exe"
        ):
            digest = bridge_setup.download_verified_binary_with_curl(
                "https://storage.googleapis.com/example/agy.exe",
                hashlib.sha512(payload).hexdigest(),
                staging,
                env={"PATH": "F:/Windows"},
                runner=runner,
            )
        self.assertEqual(digest, hashlib.sha512(payload).hexdigest())
        self.assertEqual(staging.read_bytes(), payload)
        self.assertIn("--continue-at", calls[0][0])

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

        approval = event_store.append(
            "PreToolUse",
            {
                "conversationId": "conversation-1",
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "包含敏感参数"},
                },
            },
        )
        self.assertEqual(approval["toolName"], "run_command")
        self.assertEqual(approval["approvalState"], "requested")
        self.assertNotIn("toolCall", approval)
        self.assertEqual(
            event_store.wait_approval("conversation-1", timeout_seconds=0),
            approval,
        )

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
        self.assertEqual(
            events["PreToolUse"][0]["matcher"], "run_command|ask_permission"
        )
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

    def test_setup_cli_必须显式确认完整装载(self):
        with self.assertRaises(cli.BridgeError):
            cli.setup_command(
                argparse.Namespace(setup_action="ensure", confirm_setup=False)
            )
        args = cli.build_parser().parse_args(["setup", "ensure", "--confirm-setup"])
        self.assertEqual(args.project_id, companion.DEFAULT_PROJECT_ID)
        self.assertIsNone(args.agy_dir)

    def test_审批响应要求精确事件与显式确认(self):
        with self.assertRaises(cli.BridgeError):
            cli.approval_command(
                argparse.Namespace(
                    approval_action="respond",
                    confirm_approval=False,
                )
            )

        parsed = cli.build_parser().parse_args(
            [
                "event",
                "wait-approval",
                "--conversation-id",
                "conversation-1",
                "--tool-name",
                "run_command",
            ]
        )
        self.assertEqual(parsed.timeout_seconds, 300)

    def test_desktop_审批检查与响应(self):
        button = FakeTarget("允许")
        page = FakePage(
            [button],
            role_targets={("button", "允许"): [button]},
        )
        before = {
            "open": True,
            "text": "是否允许运行此命令？\npnpm test",
            "textLength": 24,
            "buttons": ["拒绝", "允许"],
            "options": [
                {"name": "是，仅允许本次执行", "decision": "allow", "checked": False}
            ],
        }
        after = {"open": False, "text": "", "textLength": 0, "buttons": []}
        with mock.patch.object(
            control, "approval_dialog_snapshot", side_effect=[before, after]
        ):
            result = control.perform_workflow(
                page,
                argparse.Namespace(
                    workflow_action="approval-respond",
                    decision="allow",
                    button_name="允许",
                    option_name="是，仅允许本次执行",
                    timeout_ms=1000,
                    settle_ms=10,
                ),
            )
        self.assertEqual(button.clicked, 1000)
        self.assertFalse(result["detail"]["after"]["open"])

    def test_desktop_立即发送补充而不等待当前回合结束(self):
        message_input = FakeTarget()
        send_now = FakeTarget("Send Now")
        page = FakePage(
            [message_input],
            role_targets={("combobox", "Message input"): [message_input]},
        )
        with mock.patch.object(
            control,
            "queued_send_now_target",
            side_effect=[send_now, control.ControlError("队列项已消失")],
        ):
            result = control.perform_workflow(
                page,
                argparse.Namespace(
                    workflow_action="conversation-send-now",
                    prompt="立即补充",
                    timeout_ms=1000,
                    settle_ms=10,
                ),
            )
        self.assertEqual(message_input.value, "立即补充")
        self.assertEqual(message_input.pressed, ("Enter", 1000))
        self.assertEqual(send_now.clicked, 1000)
        self.assertTrue(result["detail"]["sentImmediately"])

        parsed = cli.build_parser().parse_args(
            [
                "conversation",
                "send-now",
                "--id",
                "conversation-1",
                "--prompt",
                "补充",
                "--confirm-send",
            ]
        )
        self.assertEqual(parsed.conversation_action, "send-now")

    def test_监工优先读取当前会话_review_diff(self):
        review_tab = FakeTarget("Review tab")
        review_region = FakeTarget("评审内容")
        page = FakePage(
            [review_tab],
            url="https://127.0.0.1:3900/c/test?tab=overview",
            role_targets={
                ("button", "Review tab"): [review_tab],
                ("region", "评审"): [review_region],
                ("region", "Review"): [],
            },
        )
        result = control.perform_workflow(
            page,
            argparse.Namespace(
                workflow_action="review-changes",
                timeout_ms=1000,
                settle_ms=10,
                max_controls=20,
            ),
        )
        self.assertEqual(review_tab.clicked, 1000)
        self.assertIn("snapshot", result["detail"])

        parsed = cli.build_parser().parse_args(
            ["review", "changes", "--id", "conversation-1"]
        )
        self.assertEqual(parsed.review_action, "changes")

    def test_review_diff_自动展开辅助面板(self):
        review_tab = FakeTarget("Review tab")
        toggle = FakeTarget("切换辅助面板")
        review_locator = FakeLocator([])

        def reveal_review(timeout):
            toggle.clicked = timeout
            review_locator.targets.append(review_tab)

        toggle.click = reveal_review
        page = FakePage([review_tab])

        def get_by_role(role, name, exact):
            if (role, name) == ("button", "Review tab"):
                return review_locator
            if (role, name) == ("button", "切换辅助面板"):
                return FakeLocator([toggle])
            return FakeLocator([])

        page.get_by_role = get_by_role
        result = control.review_tab_target(page, 1000)
        self.assertIs(result, review_tab)
        self.assertEqual(toggle.clicked, 1000)

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
