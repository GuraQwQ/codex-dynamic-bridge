import hashlib
import json
import uuid
from pathlib import Path

from bridge.state import (
    StateError, after_event, atomic_write_json, completed_event,
    default_data_dir, event_time, file_lock, utc_now,
)


class SubmissionStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or default_data_dir() / "submissions")

    def path(self, submission_id):
        try:
            name = str(uuid.UUID(submission_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise StateError("submission ID 必须是 UUID") from exc
        return self.directory / f"{name}.json"

    def get(self, submission_id):
        path = self.path(submission_id)
        if not path.is_file():
            raise StateError(f"未找到投递回执: {submission_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, conversation_id=None):
        records = [json.loads(path.read_text(encoding="utf-8"))
                   for path in self.directory.glob("*.json")]
        return sorted(
            (record for record in records
             if not conversation_id or record.get("conversationId") == conversation_id),
            key=lambda record: (event_time(record["submittedAt"]), record["submissionId"]), reverse=True,
        )

    def latest(self, conversation_id):
        records = self.list(conversation_id)
        return records[0] if records else None

    def update(self, submission_id, **fields):
        path = self.path(submission_id)
        with file_lock(path):
            receipt = self.get(submission_id)
            receipt.update(fields)
            atomic_write_json(path, receipt)
            return receipt

    def bind(self, submission_id, conversation_id):
        path = self.path(submission_id)
        with file_lock(path):
            receipt = self.get(submission_id)
            if receipt.get("conversationId") not in (None, conversation_id):
                raise StateError("投递回执已关联其他会话，不能更改归属")
            receipt["conversationId"] = conversation_id
            atomic_write_json(path, receipt)
            return receipt

    def dispatch(self, action, backend, conversation_id, callback, tasks):
        receipt = {
            "submissionId": str(uuid.uuid4()), "conversationId": conversation_id,
            "action": action, "backend": backend, "submittedAt": utc_now(),
            "delivery": "dispatching",
        }
        path = self.path(receipt["submissionId"])
        atomic_write_json(path, receipt)
        if conversation_id:
            tasks.upsert({"conversationId": conversation_id,
                          "submissionId": receipt["submissionId"],
                          "lastSubmittedAt": receipt["submittedAt"], "status": "dispatching"})
        try:
            result = callback()
            conversation_id = (
                result.get("conversationId") or result.get("conversation_id")
                or result.get("detail", {}).get("conversationId") or conversation_id
            )
        except BaseException as exc:
            receipt = self.update(receipt["submissionId"], delivery="outcome_unknown")
            if conversation_id:
                tasks.upsert({"conversationId": conversation_id,
                              "lastSubmittedAt": receipt["submittedAt"], "status": "outcome_unknown"})
            if isinstance(exc, Exception):
                raise StateError(
                    f"投递结果待核对，回执 {receipt['submissionId']}；不要自动重发: {exc}"
                ) from exc
            raise
        receipt = self.update(receipt["submissionId"], conversationId=conversation_id, delivery="accepted")
        if conversation_id:
            tasks.upsert({"conversationId": conversation_id,
                          "submissionId": receipt["submissionId"],
                          "lastSubmittedAt": receipt["submittedAt"], "status": "submitted"})
        return result, receipt

    def inspect(self, events, conversation_id=None, submission_id=None):
        receipt = self.get(submission_id) if submission_id else self.latest(conversation_id)
        if receipt:
            conversation_id = receipt.get("conversationId")
        observed = events.list(conversation_id, limit=None) if conversation_id else []
        if receipt:
            observed = [event for event in observed if after_event(event, receipt["submittedAt"])]
            later = [event_time(item["submittedAt"]) for item in self.list(conversation_id)
                     if event_time(item["submittedAt"]) > event_time(receipt["submittedAt"])]
            if later:
                observed = [event for event in observed if event_time(event["observedAt"]) < min(later)]
        latest = observed[-1] if observed else None
        execution = "unobserved"
        if completed_event(observed):
            execution = "stopped"
        elif latest:
            execution = "waiting_approval" if latest.get("approvalState") == "requested" else "running"
        review = dict((receipt or {}).get("review", {"verdict": "unverified"}))
        if review.get("evidencePath"):
            try:
                matches = self.evidence_digest(Path(review["evidencePath"])) == review["sha256"]
            except OSError:
                matches = False
            if not matches:
                review["recordedVerdict"] = review["verdict"]
                review["verdict"] = "stale"
        return {"conversationId": conversation_id, "submission": receipt,
                "execution": execution, "latestEvent": latest, "review": review,
                "eventAttribution": "after_submission" if receipt else "untracked"}

    @staticmethod
    def evidence_digest(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()

    def record_review(self, submission_id, verdict, evidence):
        if verdict not in {"passed", "failed"}:
            raise StateError("验收结论必须是 passed 或 failed")
        evidence = Path(evidence).resolve()
        digest = self.evidence_digest(evidence)
        path = self.path(submission_id)
        with file_lock(path):
            receipt = self.get(submission_id)
            receipt["review"] = {"verdict": verdict, "evidencePath": str(evidence),
                                 "sha256": digest, "recordedAt": utc_now()}
            atomic_write_json(path, receipt)
        return receipt


def read_checkpoint(path):
    if not path.exists():
        return {"cursor": 0, "streamId": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        value = None
    if (not isinstance(value, dict) or type(value.get("cursor")) is not int
            or value["cursor"] < 0 or not isinstance(value.get("streamId"), str)):
        # 进度可由原日志重建，成功重放一页后才替换损坏的进度文件。
        return {"cursor": 0, "streamId": None, "recovered": True}
    return value


def sync_events(client, store, tasks, conversation_id=None):
    scope = json.dumps([str(client.endpoint_file.resolve()), conversation_id])
    key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    checkpoint = store.path.parent / "sync" / f"{key}.json"
    imported_count = 0
    updated = {}
    recovered = False
    while True:
        before = read_checkpoint(checkpoint)
        recovered = recovered or before.get("recovered", False)
        page = client.event_page(
            conversation_id, limit=1000, after=before["cursor"], stream_id=before["streamId"]
        )
        if not page.get("streamId"):
            raise StateError("增量同步需要新版 Companion；请更新后重试")
        if page["hasMore"] and page["nextCursor"] <= (0 if page.get("reset") else before["cursor"]):
            raise StateError("Sidecar 事件游标未向前推进")
        with file_lock(checkpoint):
            if read_checkpoint(checkpoint) != before:
                continue
            imported_count += len(store.import_events(page["events"]))
            # 即使事件已导入也重放任务更新，修复上次在两个文件之间中断的状态。
            for task in tasks.sync_events(page["events"]):
                updated[task["conversationId"]] = task
            current = {"cursor": page["nextCursor"], "streamId": page["streamId"]}
            atomic_write_json(checkpoint, current)
        if not page["hasMore"]:
            return {"imported": imported_count, "tasks": list(updated.values()),
                    "checkpoint": current, "checkpointRecovered": recovered}
