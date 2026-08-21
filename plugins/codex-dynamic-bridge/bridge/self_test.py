import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bridge import cli, control


class FakeTarget:
    def __init__(self, text="目标", editable_error=False):
        self.text = text
        self.editable_error = editable_error
        self.value = ""
        self.clicked = False
        self.pressed = None
        self.waited = None

    def evaluate(self, _):
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


class FakePage:
    def __init__(self, targets):
        self.targets = targets
        self.settled = None

    def locator(self, _):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
