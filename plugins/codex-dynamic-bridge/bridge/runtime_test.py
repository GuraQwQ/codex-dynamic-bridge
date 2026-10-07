import json
import subprocess
import sys
import time
import unittest

from bridge.runtime import AgyClient, RuntimeBridgeError


INIT = {"event": "init", "conversation_id": "test-conversation", "init": {"cwd": "/workspace"}}
RESULT = {"event": "result", "result": {"conversation_id": "test-conversation", "status": "SUCCESS", "response": "完成"}}


class AgyRuntimeTest(unittest.TestCase):
    def client_for_script(self, script):
        processes = []

        def start(command, **kwargs):
            self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
            process = subprocess.Popen([sys.executable, "-u", "-c", script], **kwargs)
            processes.append(process)
            return process

        return AgyClient(executable=sys.executable, popen=start), processes

    def test_json_diagnostics_and_model_output(self):
        def run(command, **kwargs):
            if command[-1] == "models":
                return subprocess.CompletedProcess(command, 0, "model-a\n", "进度\n")
            return subprocess.CompletedProcess(command, 0, json.dumps(RESULT["result"]), "x" * 9000 + "权限请求被拒绝\n")

        client = AgyClient(executable="agy", runner=run)
        result = client.run_prompt("测试")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(result["diagnostics"].endswith("权限请求被拒绝"))
        self.assertLessEqual(len(result["diagnostics"]), 8192)
        self.assertEqual(result["response"], "完成")
        self.assertEqual(client.list_models()["models"], ["model-a"])

    def test_capability_flags_and_probe_failure(self):
        help_text = "--project --conversation --new-project --model --effort --agent --output-format [text,json,stream-json] --json-schema"

        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "agy version 1.3.1\n" if command[-1] == "--version" else "", "" if command[-1] == "--version" else help_text)

        client = AgyClient(executable="agy", runner=run)
        capabilities = client.capabilities()
        self.assertTrue(capabilities["available"])
        self.assertEqual(capabilities["version"], "1.3.1")
        self.assertTrue(all(capabilities["flags"].values()))
        help_text = "--new-project --model-alias"
        capabilities = client.capabilities()
        self.assertTrue(capabilities["flags"]["new-project"])
        self.assertFalse(capabilities["flags"]["project"])
        self.assertFalse(capabilities["flags"]["model"])
        client.runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "无法探测")
        capabilities = client.capabilities()
        self.assertFalse(capabilities["available"])
        self.assertFalse(any(capabilities["flags"].values()))
        self.assertIn("probeError", capabilities)

    def test_stream_chunks_stderr_and_early_init(self):
        init_line = json.dumps(INIT) + "\n"
        step = {"event": "step_update", "step_update": {"conversation_id": "test-conversation", "state": "ACTIVE", "text_delta": "内容"}}
        script = (
            "import sys,time\n"
            f"sys.stdout.write({init_line[:15]!r});sys.stdout.flush()\n"
            "time.sleep(0.02)\n"
            f"sys.stdout.write({init_line[15:]!r});sys.stdout.flush()\n"
            "sys.stderr.write('x'*131072+'诊断尾部');sys.stderr.flush()\n"
            "time.sleep(0.2)\n"
            f"print('\\n'+{json.dumps(step)!r},flush=True)\n"
            f"print({json.dumps(RESULT)!r},flush=True)\n"
        )
        client, processes = self.client_for_script(script)
        events = []

        def callback(event):
            if event["event"] == "init":
                self.assertIsNone(processes[0].poll())
            events.append(event)

        result = client.run_prompt("测试", stream=True, on_event=callback, timeout_seconds=5)
        self.assertEqual([event["event"] for event in events], ["init", "step_update", "result"])
        self.assertEqual(result["response"], "完成")
        self.assertLessEqual(len(result["diagnostics"]), 8192)
        self.assertTrue(result["diagnostics"].endswith("诊断尾部"))
        self.assertEqual(processes[0].poll(), 0)
        self.assertTrue(processes[0].stdout.closed)
        self.assertTrue(processes[0].stderr.closed)

    def test_stream_protocol_and_exit_failures_do_not_retry(self):
        init_statement = f"print({json.dumps(INIT)!r},flush=True)\n"
        cases = [
            ("print('not-json',flush=True)", "无效 JSON"),
            (init_statement, "缺少 result"),
            (init_statement + f"print({json.dumps(RESULT)!r},flush=True)\nraise SystemExit(7)", "退出码 7"),
        ]
        for body, message in cases:
            with self.subTest(message=message):
                client, processes = self.client_for_script(body)
                with self.assertRaisesRegex(RuntimeBridgeError, message):
                    client.run_prompt("测试", stream=True, timeout_seconds=3)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())

    def test_timeout_and_callback_cancellation_cleanup(self):
        init_statement = f"print({json.dumps(INIT)!r},flush=True)\n"
        script = "import time\n" + init_statement + "time.sleep(30)\n"
        client, processes = self.client_for_script(script)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeBridgeError, "超时"):
            client.run_prompt("测试", stream=True, timeout_seconds=0.15)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNotNone(processes[0].poll())

        for error in (ValueError("回调错误"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                client, processes = self.client_for_script(script)

                def cancel(event):
                    raise error

                expected = RuntimeBridgeError if isinstance(error, Exception) else KeyboardInterrupt
                with self.assertRaises(expected):
                    client.run_prompt("测试", stream=True, on_event=cancel, timeout_seconds=3)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())
                self.assertTrue(processes[0].stdout.closed)
                self.assertTrue(processes[0].stderr.closed)

    def test_stream_timeout_preserves_short_split_utf8_diagnostics(self):
        diagnostic = "模型请求仍在等待，尚未开始会话"
        encoded = diagnostic.encode("utf-8")
        script = (
            "import sys,time\n"
            f"sys.stderr.buffer.write({encoded[:2]!r});sys.stderr.buffer.flush()\n"
            "time.sleep(0.02)\n"
            f"sys.stderr.buffer.write({encoded[2:]!r});sys.stderr.buffer.flush()\n"
            "time.sleep(30)\n"
        )
        client, processes = self.client_for_script(script)
        with self.assertRaisesRegex(RuntimeBridgeError, "超时") as caught:
            client.run_prompt("测试", stream=True, timeout_seconds=0.3)
        self.assertIn(diagnostic, str(caught.exception))
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdout.closed)
        self.assertTrue(processes[0].stderr.closed)


if __name__ == "__main__":
    unittest.main()
