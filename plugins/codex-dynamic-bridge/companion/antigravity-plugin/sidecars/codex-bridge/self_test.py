import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


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
