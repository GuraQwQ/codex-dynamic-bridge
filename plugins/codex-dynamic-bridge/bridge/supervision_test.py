import json
import io
import importlib.util
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from bridge import state


class SupervisionTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = mock.patch.dict(os.environ, {"CODEX_DYNAMIC_BRIDGE_DATA_DIR": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.events = state.EventStore(self.root / "events.jsonl")
        self.tasks = state.TaskStore(self.root / "tasks.json")
        self.client = mock.Mock(endpoint_file=self.root / "endpoint.json")

    def page(self, cursor, events=None, more=False, stream="log-1", reset=False):
        return {"events": events or [], "nextCursor": cursor, "hasMore": more,
                "streamId": stream, "reset": reset}

    def test_增量同步持久化游标并区分会话过滤范围(self):
        from bridge.supervision import sync_events

        event = {"conversationId": "c1", "kind": "Stop", "fullyIdle": True,
                 "observedAt": "2026-09-05T00:00:00Z"}
        self.client.event_page.side_effect = [self.page(4, [event]), self.page(4), self.page(4)]
        result = sync_events(self.client, self.events, self.tasks)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(sync_events(self.client, self.events, self.tasks)["imported"], 0)
        self.assertEqual(self.client.event_page.call_args.kwargs["after"], 4)
        sync_events(self.client, self.events, self.tasks, "c1")
        self.assertEqual(self.client.event_page.call_args.kwargs["after"], 0)

    def test_任务落盘失败后重试能修复且不重复导入事件(self):
        from bridge.supervision import sync_events

        event = {"conversationId": "c1", "kind": "Stop", "fullyIdle": True,
                 "observedAt": "2026-09-05T00:00:00Z"}
        self.client.event_page.return_value = self.page(1, [event])
        with mock.patch.object(self.tasks, "sync_events", side_effect=OSError("中断"), create=True):
            with self.assertRaises(OSError):
                sync_events(self.client, self.events, self.tasks)
        result = sync_events(self.client, self.events, self.tasks)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(self.client.event_page.call_args.kwargs["after"], 0)
        self.assertEqual(self.tasks.load()[0]["status"], "idle")
        self.assertEqual(len(self.events.list()), 1)

    def test_投递先记回执失败不重发且不保存提示词(self):
        from bridge.supervision import SubmissionStore

        store = SubmissionStore(self.root / "submissions")

        def interrupted():
            self.assertEqual(store.list()[0]["delivery"], "dispatching")
            raise TimeoutError("连接中断")

        callback = mock.Mock(side_effect=interrupted)
        with self.assertRaisesRegex(state.StateError, "不要自动重发"):
            store.dispatch("send", "sidecar", "c1", callback, self.tasks)
        callback.assert_called_once()
        receipt = store.list()[0]
        self.assertEqual(receipt["delivery"], "outcome_unknown")
        self.assertEqual(receipt["conversationId"], "c1")
        self.assertNotIn("prompt", receipt)

    def test_已接受与已停止和验收分离(self):
        from bridge.supervision import SubmissionStore

        store = SubmissionStore(self.root / "submissions")
        with mock.patch("bridge.supervision.utc_now", return_value="2026-09-05T00:00:00.500000Z"):
            _, receipt = store.dispatch(
                "send", "sidecar", "c1", lambda: {"status": "ok"}, self.tasks
            )
        self.events.import_events([
            {"kind": "Stop", "conversationId": "c1", "fullyIdle": True,
             "observedAt": "2026-09-05T00:00:00Z"},
        ])
        inspected = store.inspect(self.events, submission_id=receipt["submissionId"])
        self.assertEqual(inspected["execution"], "unobserved")
        self.events.import_events([
            {"kind": "Stop", "conversationId": "c1", "fullyIdle": True,
             "observedAt": "2026-09-05T00:00:00.600000Z"},
        ])
        inspected = store.inspect(self.events, conversation_id="c1")
        self.assertEqual(inspected["execution"], "stopped")
        self.assertEqual(inspected["review"]["verdict"], "unverified")
        self.assertEqual(self.tasks.load()[0]["submissionId"], receipt["submissionId"])

    def test_旧完成后出现新活动不能报告完成(self):
        self.events.import_events([
            {"kind": "Stop", "conversationId": "c1", "fullyIdle": True,
             "observedAt": "2026-09-05T00:00:00.100000Z"},
            {"kind": "PreInvocation", "conversationId": "c1",
             "observedAt": "2026-09-05T00:00:00.200000Z"},
        ])
        with self.assertRaises(state.StateError):
            self.events.wait("c1", timeout_seconds=0)

    def test_验收记录绑定投递且证据变更后失效(self):
        from bridge.supervision import SubmissionStore

        store = SubmissionStore(self.root / "submissions")
        _, receipt = store.dispatch("new", "sidecar", None, lambda: {"conversationId": "c1"}, self.tasks)
        evidence = self.root / "test-result.txt"
        evidence.write_text("tests passed", encoding="utf-8")
        store.record_review(receipt["submissionId"], "passed", evidence)
        inspected = store.inspect(self.events, conversation_id="c1")
        self.assertEqual(inspected["review"]["verdict"], "passed")
        evidence.write_text("different results", encoding="utf-8")
        self.assertEqual(store.inspect(self.events, conversation_id="c1")["review"]["verdict"], "stale")

    def test_cli_发送回执自动绑定两个等待入口(self):
        from bridge import cli

        args = cli.build_parser().parse_args([
            "conversation", "send", "--conversation-id", "c1", "--prompt", "任务",
            "--backend", "sidecar", "--confirm-send",
        ])
        self.client.send_message.return_value = {"status": "ok"}
        with mock.patch.object(cli, "select_runtime_backend", return_value=("sidecar", self.client)):
            with redirect_stdout(io.StringIO()):
                sent = args.func(args)
        after = sent["submission"]["submittedAt"]
        for command in ("conversation", "event"):
            wait_args = [command, "wait", "--conversation-id", "c1", "--timeout-seconds", "0"]
            if command == "conversation":
                wait_args += ["--backend", "local"]
            parsed = cli.build_parser().parse_args(wait_args)
            with mock.patch.object(state.EventStore, "wait", return_value={}) as wait:
                with redirect_stdout(io.StringIO()):
                    parsed.func(parsed)
            self.assertEqual(wait.call_args.kwargs["after"], after)

    def test_原子导入失败保留旧日志且重试可完成(self):
        old = {"kind": "Stop", "conversationId": "c1", "observedAt": "2026-09-05T00:00:00Z"}
        new = {**old, "observedAt": "2026-09-05T00:00:01Z"}
        self.events.import_events([old])
        before = self.events.path.read_bytes()
        with mock.patch.object(state.os, "replace", side_effect=OSError("中断")):
            with self.assertRaises(OSError):
                self.events.import_events([new])
        self.assertEqual(self.events.path.read_bytes(), before)
        self.assertEqual(len(self.events.import_events([new])), 1)

    def test_时间偏移与小数秒按绝对时间比较(self):
        event = {"observedAt": "2026-09-05T08:00:00.100000+08:00"}
        self.assertTrue(state.after_event(event, "2026-09-05T00:00:00Z"))
        self.assertFalse(state.after_event(event, "2026-09-05T00:00:00.200000Z"))

    def test_http_旧日志分页导入重复同步及新事件(self):
        from bridge.runtime import SidecarClient
        from bridge.supervision import sync_events

        server_path = (Path(__file__).resolve().parents[1] / "companion" /
                       "antigravity-plugin" / "sidecars" / "codex-bridge" / "server.py")
        spec = importlib.util.spec_from_file_location("isolated_sidecar", server_path)
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        server.EVENTS_PATH = self.root / "source-events.jsonl"
        records = [
            {"conversationId": "c1", "kind": "Stop" if index == 1004 else "PostInvocation",
             "fullyIdle": index == 1004, "stepIdx": index,
             "observedAt": f"2026-09-05T00:00:00.{index:06d}Z"}
            for index in range(1005)
        ]
        original = "".join(json.dumps(record) + "\n" for record in records)
        server.EVENTS_PATH.write_text(original, encoding="utf-8")
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.token = "isolated-test-token"
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        endpoint = self.root / "endpoint.json"
        state.atomic_write_json(endpoint, {"url": f"http://127.0.0.1:{httpd.server_port}",
                                           "token": httpd.token})
        client = SidecarClient(endpoint_file=endpoint)
        first = sync_events(client, self.events, self.tasks)
        self.assertEqual(first["imported"], 1005)
        self.assertEqual(first["checkpoint"]["cursor"], 1005)
        self.assertEqual(self.tasks.load()[0]["status"], "idle")
        self.assertEqual(server.EVENTS_PATH.read_text(encoding="utf-8"), original)
        self.assertEqual(sync_events(client, self.events, self.tasks)["imported"], 0)
        server.append_event({"conversationId": "c1", "kind": "PreInvocation",
                             "observedAt": "2026-09-05T00:00:01Z"})
        self.assertEqual(sync_events(client, self.events, self.tasks)["imported"], 1)
        self.assertEqual(self.tasks.load()[0]["status"], "running")
        self.assertEqual(len(self.events.list(limit=None)), 1006)

    def test_回放历史事件不能覆盖本次投递状态(self):
        from bridge.supervision import SubmissionStore

        self.tasks.sync_event({"conversationId": "c1", "fullyIdle": True,
                               "observedAt": "2026-09-05T00:00:00Z"})
        with mock.patch("bridge.supervision.utc_now", return_value="2026-09-05T00:00:01Z"):
            SubmissionStore().dispatch("send", "sidecar", "c1", lambda: {}, self.tasks)
        self.tasks.sync_event({"conversationId": "c1", "fullyIdle": True,
                               "observedAt": "2026-09-05T00:00:00.5Z"})
        self.assertEqual(self.tasks.load()[0]["status"], "submitted")

    def test_交错投递保留最新任务绑定和已写入验收(self):
        from bridge.supervision import SubmissionStore

        store = SubmissionStore()
        evidence = self.root / "result.txt"
        evidence.write_text("passed", encoding="utf-8")

        def first_callback():
            first = store.latest("c1")
            store.record_review(first["submissionId"], "passed", evidence)
            with mock.patch("bridge.supervision.utc_now", return_value="2026-09-05T00:00:02Z"):
                store.dispatch("send", "sidecar", "c1", lambda: {}, self.tasks)
            return {}

        with mock.patch("bridge.supervision.utc_now", return_value="2026-09-05T00:00:01Z"):
            _, first = store.dispatch("send", "sidecar", "c1", first_callback, self.tasks)
        self.assertEqual(store.get(first["submissionId"])["review"]["verdict"], "passed")
        self.assertEqual(self.tasks.load()[0]["submissionId"], store.latest("c1")["submissionId"])

    def test_历史投递不会混入后续投递事件(self):
        from bridge.supervision import SubmissionStore

        store = SubmissionStore()
        with mock.patch("bridge.supervision.utc_now", return_value="2026-09-05T00:00:01Z"):
            _, first = store.dispatch("send", "sidecar", "c1", lambda: {}, self.tasks)
        with mock.patch("bridge.supervision.utc_now", return_value="2026-09-05T00:00:03Z"):
            store.dispatch("send", "sidecar", "c1", lambda: {}, self.tasks)
        self.events.import_events([
            {"kind": "Stop", "conversationId": "c1", "fullyIdle": True,
             "observedAt": "2026-09-05T00:00:02Z"},
            {"kind": "PreInvocation", "conversationId": "c1",
             "observedAt": "2026-09-05T00:00:04Z"},
        ])
        self.assertEqual(store.inspect(self.events, submission_id=first["submissionId"])["execution"], "stopped")

    def test_sidecar_乱序事件不误报旧完成(self):
        from bridge.runtime import SidecarClient, RuntimeBridgeError

        client = SidecarClient()
        events = [
            {"kind": "PreInvocation", "observedAt": "2026-09-05T00:00:02Z"},
            {"kind": "Stop", "fullyIdle": True, "observedAt": "2026-09-05T00:00:01Z"},
        ]
        with mock.patch.object(client, "list_events", return_value=events):
            with self.assertRaises(RuntimeBridgeError):
                client.wait("c1", timeout_seconds=0)

    def test_损坏进度重建不丢失已有事件(self):
        from bridge.supervision import sync_events

        event = {"conversationId": "c1", "kind": "Stop", "observedAt": "2026-09-05T00:00:00Z"}
        self.client.event_page.return_value = self.page(1, [event])
        sync_events(self.client, self.events, self.tasks)
        checkpoint = next((self.root / "sync").glob("*.json"))
        checkpoint.write_text("{", encoding="utf-8")
        restored = sync_events(self.client, self.events, self.tasks)
        self.assertTrue(restored["checkpointRecovered"])
        self.assertEqual(restored["imported"], 0)
        self.assertEqual(json.loads(checkpoint.read_text(encoding="utf-8"))["cursor"], 1)

    def test_中断新建可补全归属但不能改绑(self):
        from bridge.supervision import SubmissionStore

        store = SubmissionStore()
        with self.assertRaises(state.StateError):
            store.dispatch("new", "sidecar", None, mock.Mock(side_effect=TimeoutError()), self.tasks)
        receipt = store.list()[0]
        bound = store.bind(receipt["submissionId"], "c1")
        self.assertEqual(bound["delivery"], "outcome_unknown")
        self.assertEqual(store.latest("c1")["submissionId"], receipt["submissionId"])
        with self.assertRaises(state.StateError):
            store.bind(receipt["submissionId"], "c2")

    def test_cli_无确认不能记录验收(self):
        from bridge import cli

        parsed = cli.build_parser().parse_args([
            "task", "record-review", "--submission-id", "placeholder",
            "--verdict", "passed", "--evidence", "not-read.txt",
        ])
        with self.assertRaises(cli.BridgeError):
            parsed.func(parsed)


if __name__ == "__main__":
    unittest.main()
