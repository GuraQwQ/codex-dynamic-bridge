import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
import event_sink


class SidecarTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        server.DATA_DIR = root
        server.EVENTS_PATH = root / "events.jsonl"
        server.SCHEDULES_PATH = root / "schedules.json"
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.token = "test-token"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temporary_directory.cleanup()

    def request(self, method, path, payload=None, authorized=True):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = "Bearer test-token"
        request = Request(self.url + path, data=data, method=method, headers=headers)
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_需要认证(self):
        with self.assertRaises(HTTPError) as context:
            self.request("GET", "/v1/health", authorized=False)
        self.assertEqual(context.exception.code, 401)
        status, result = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "ok")

    def test_event_净化并可查询(self):
        status, event = self.request(
            "POST",
            "/v1/events",
            {
                "kind": "Stop",
                "conversationId": "conversation-1",
                "fullyIdle": True,
                "token": "不应保存",
            },
        )
        self.assertEqual(status, 200)
        self.assertNotIn("token", event)
        _, result = self.request("GET", "/v1/events?conversation_id=conversation-1")
        self.assertEqual(len(result["events"]), 1)

    def test_event_可按游标分页且不丢失(self):
        for step_idx in range(5):
            self.request(
                "POST",
                "/v1/events",
                {
                    "kind": "PostToolUse",
                    "conversationId": "conversation-1",
                    "stepIdx": step_idx,
                },
            )

        _, latest = self.request("GET", "/v1/events?limit=2")
        self.assertEqual([event["stepIdx"] for event in latest["events"]], [3, 4])

        _, first = self.request("GET", "/v1/events?limit=2&after=0")
        self.assertEqual([event["stepIdx"] for event in first["events"]], [0, 1])
        self.assertTrue(first["hasMore"])

        _, second = self.request(
            "GET", f"/v1/events?limit=2&after={first['nextCursor']}"
        )
        self.assertEqual([event["stepIdx"] for event in second["events"]], [2, 3])
        self.assertTrue(second["hasMore"])

        _, last = self.request(
            "GET", f"/v1/events?limit=2&after={second['nextCursor']}"
        )
        self.assertEqual([event["stepIdx"] for event in last["events"]], [4])
        self.assertFalse(last["hasMore"])

    def test_pre_tool_use_只保留工具名和审批状态(self):
        event = event_sink.sanitize_event(
            "PreToolUse",
            {
                "conversationId": "conversation-1",
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "secret command"},
                },
            },
        )
        self.assertEqual(event["toolName"], "run_command")
        self.assertEqual(event["approvalState"], "requested")
        self.assertNotIn("toolCall", event)

    def test_event_空状态及日志替换后恢复游标(self):
        _, empty = self.request("GET", "/v1/events?after=0")
        self.assertEqual(empty["nextCursor"], 0)
        self.request("POST", "/v1/events", {"conversationId": "c1", "kind": "Stop"})
        _, first = self.request("GET", "/v1/events?after=0")
        old_stream = first["streamId"]
        server.EVENTS_PATH.rename(server.EVENTS_PATH.with_suffix(".old"))
        self.request("POST", "/v1/events", {"conversationId": "c2", "kind": "Stop"})
        _, reset = self.request(
            "GET", f"/v1/events?after={first['nextCursor']}&stream_id={old_stream}"
        )
        self.assertTrue(reset["reset"])
        self.assertEqual(reset["events"][0]["conversationId"], "c2")
        self.assertNotEqual(reset["streamId"], old_stream)

    def test_event_过滤空页推进游标且截断后重读(self):
        for index in range(3):
            self.request("POST", "/v1/events", {"conversationId": "other", "stepIdx": index})
        _, page = self.request("GET", "/v1/events?after=0&conversation_id=c1")
        self.assertEqual(page["events"], [])
        self.assertEqual(page["nextCursor"], 3)
        server.EVENTS_PATH.write_text("", encoding="utf-8")
        self.request("POST", "/v1/events", {"conversationId": "c1", "kind": "Stop"})
        _, reset = self.request("GET", f"/v1/events?after=3&stream_id={page['streamId']}")
        self.assertTrue(reset["reset"])
        self.assertEqual(len(reset["events"]), 1)

    def test_schedule_创建列出删除(self):
        status, schedule = self.request(
            "POST",
            "/v1/schedules",
            {"prompt": "检查项目", "intervalSeconds": 60},
        )
        self.assertEqual(status, 201)
        _, result = self.request("GET", "/v1/schedules")
        self.assertEqual(len(result["schedules"]), 1)
        _, removed = self.request("DELETE", f"/v1/schedules/{schedule['id']}")
        self.assertEqual(removed["removed"], schedule["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
