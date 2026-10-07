import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from bridge import cli, state
from bridge.supervision import SubmissionStore


class StreamSupervisionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"CODEX_DYNAMIC_BRIDGE_DATA_DIR": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.events = state.EventStore()
        self.tasks = state.TaskStore()
        self.receipts = SubmissionStore()

    def test_初始化后中断仍能定位会话且不保留正文(self):
        def interrupted(observe):
            observe({"event": "init", "conversation_id": "main", "init": {"cwd": "F:/repo"}})
            self.assertEqual(self.receipts.list()[0]["conversationId"], "main")
            self.assertEqual(self.tasks.load()[0]["workspacePaths"], ["F:/repo"])
            observe({"event": "step_update", "step_update": {
                "conversation_id": "main", "step_index": 1, "state": "ACTIVE",
                "text_delta": "正文不能入账", "tool_name": "run_command",
                "tool_info": {"parameters": {"CommandLine": "参数不能入账"}, "output": "输出不能入账"},
            }})
            for text in ("分块一", "分块二", "分块三"):
                observe({"event": "step_update", "step_update": {
                    "conversation_id": "main", "step_index": 1, "state": "ACTIVE",
                    "tool_name": "run_command", "text_delta": text,
                }})
            self.assertEqual(len(self.events.list("main")), 2)
            raise TimeoutError("中断")

        with self.assertRaisesRegex(state.StateError, "不要自动重发"):
            self.receipts.dispatch("new", "agy", None, interrupted, self.tasks, events=self.events)
        receipt = self.receipts.list()[0]
        self.assertEqual(receipt["conversationId"], "main")
        self.assertEqual(receipt["delivery"], "outcome_unknown")
        self.assertEqual(self.receipts.inspect(self.events, submission_id=receipt["submissionId"])["execution"], "running")
        stored = json.dumps(self.events.list(), ensure_ascii=False)
        for omitted in ("正文不能入账", "参数不能入账", "输出不能入账", "text_delta", "tool_info"):
            self.assertNotIn(omitted, stored)

    def test_原生回合返回和完全空闲分别观察(self):
        def invoke(observe):
            observe({"event": "init", "conversation_id": "main", "init": {"cwd": "F:/repo"}})
            observe({"event": "result", "result": {"conversation_id": "main", "status": "SUCCESS"}})
            return {"conversation_id": "main", "status": "SUCCESS"}

        _, receipt = self.receipts.dispatch("new", "agy", None, invoke, self.tasks, events=self.events)
        self.assertEqual(self.receipts.inspect(self.events, submission_id=receipt["submissionId"])["execution"], "turn_completed")
        with self.assertRaises(state.StateError):
            self.events.wait("main", timeout_seconds=0)
        self.events.append("Stop", {"conversationId": "main", "fullyIdle": True, "executionNum": 4})
        self.events.append("CliResult", {"conversationId": "main", "cliStatus": "SUCCESS"})
        self.assertEqual(self.events.summary("main")["status"], "idle")
        self.assertEqual(self.receipts.inspect(self.events, submission_id=receipt["submissionId"])["execution"], "stopped")
        self.events.append("PreInvocation", {"conversationId": "main", "invocationNum": 5, "initialNumSteps": 8})
        self.assertEqual(self.events.summary("main")["status"], "running")

    def test_子会话和嵌套工作区归属及重放(self):
        records = state.native_stream_events({"event": "step_update", "step_update": {
            "conversation_id": "main", "step_index": 2, "state": "DONE",
            "tool_name": "invoke_subagent", "subagent_info": {"subagents": [{
                "conversation_id": "worker", "role": "实现", "type_name": "self",
                "workspace_uris": ["file:///F:/work%20tree", "vscode-remote://wsl/Ubuntu/repo"],
                "log_uri": "不保存子 Agent 日志",
            }]},
        }}, "main")
        records += state.native_stream_events({"event": "step_update", "step_update": {
            "conversation_id": "worker", "subagent_info": {"subagents": [{
                "conversation_id": "reviewer", "role": "验收", "workspace_uris": ["file:///F:/review"],
            }]},
        }}, "main")
        self.events.import_events(records)
        self.tasks.sync_events(records)
        self.assertEqual(self.events.import_events(records), [])
        self.events.append("Stop", {"conversationId": "worker", "fullyIdle": True})
        summary = self.events.summary("main")
        agents = {item["conversationId"]: item for item in summary["agents"]}
        self.assertEqual(set(agents), {"worker", "reviewer"})
        self.assertEqual(agents["worker"]["parentConversationId"], "main")
        self.assertEqual(agents["worker"]["workspacePaths"], ["F:/work tree"])
        self.assertEqual(agents["worker"]["execution"], "stopped")
        self.assertEqual(agents["reviewer"]["parentConversationId"], "worker")
        tasks = {item["conversationId"]: item for item in self.tasks.load()}
        self.assertEqual(tasks["worker"]["agentRole"], "实现")
        self.assertNotIn("log_uri", json.dumps(self.events.list()))

    def test_cli_流式投递接回执与任务账本(self):
        client = mock.Mock()
        client.capabilities.return_value = {"flags": {"stream-json": True}}

        def run(prompt, **options):
            self.assertTrue(options["stream"])
            options["on_event"]({"event": "init", "conversation_id": "main", "init": {"cwd": "F:/repo"}})
            self.assertEqual(self.receipts.list()[0]["conversationId"], "main")
            options["on_event"]({"event": "result", "result": {"conversation_id": "main", "status": "SUCCESS"}})
            return {"conversation_id": "main", "status": "SUCCESS", "diagnostics": "工具未执行的提示"}

        client.run_prompt.side_effect = run
        args = cli.build_parser().parse_args([
            "conversation", "new", "--backend", "agy", "--stream", "--prompt", "任务", "--confirm-create",
        ])
        with mock.patch.object(cli, "select_runtime_backend", return_value=("agy", client)), redirect_stdout(io.StringIO()):
            result = args.func(args)
        self.assertEqual(result["submission"]["delivery"], "accepted")
        self.assertEqual(result["task"]["status"], "turn_completed")
        self.assertEqual(result["result"]["diagnostics"], "工具未执行的提示")
        client.run_prompt.assert_called_once()

    def test_明确选择后端和初始化归属冲突不投递重试(self):
        args = cli.build_parser().parse_args([
            "conversation", "send", "--conversation-id", "main", "--stream", "--prompt", "任务", "--confirm-send",
        ])
        with mock.patch.object(cli, "select_runtime_backend") as select:
            with self.assertRaises(cli.BridgeError):
                args.func(args)
            select.assert_not_called()

        def mismatch(observe):
            observe({"event": "init", "conversation_id": "other", "init": {}})

        with self.assertRaisesRegex(state.StateError, "不要自动重发"):
            self.receipts.dispatch("send", "agy", "main", mismatch, self.tasks, events=self.events)
        self.assertEqual(self.receipts.list()[0]["conversationId"], "main")
        self.assertEqual(self.events.list(), [])

    def test_最终返回和最终事件不能改变已绑定会话(self):
        for through_event in (False, True):
            def mismatch(observe):
                observe({"event": "init", "conversation_id": "main", "init": {}})
                result = {"conversation_id": "other", "status": "SUCCESS"}
                if through_event:
                    observe({"event": "result", "result": result})
                return result

            with self.assertRaisesRegex(state.StateError, "不要自动重发"):
                self.receipts.dispatch("new", "agy", None, mismatch, self.tasks, events=self.events)
            receipt = self.receipts.list()[0]
            self.assertEqual(receipt["conversationId"], "main")
            self.assertEqual(receipt["delivery"], "outcome_unknown")
            self.assertEqual(self.events.list("other"), [])

    def test_晚到关系信息不遮蔽更早的真实停止事件(self):
        metadata = {"kind": "Subagent", "conversationId": "child", "parentConversationId": "main",
                    "workspacePaths": ["F:/tree"], "observedAt": "2026-10-07T01:00:02Z"}
        stopped = {"kind": "Stop", "conversationId": "child", "fullyIdle": True,
                   "observedAt": "2026-10-07T01:00:01Z"}
        self.tasks.sync_events([metadata])
        self.tasks.sync_events([stopped])
        task = self.tasks.load()[0]
        self.assertEqual(task["status"], "idle")
        self.assertEqual(task["lastObservedAt"], stopped["observedAt"])
        self.assertEqual(task["parentConversationId"], "main")
        self.assertEqual(task["workspacePaths"], ["F:/tree"])


if __name__ == "__main__":
    unittest.main()
