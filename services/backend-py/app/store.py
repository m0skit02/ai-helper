from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from .schemas import ActionConfirmRequest, ActionItem, TaskCreateRequest, TaskResponse, TaskResult, TraceItem


def requires_message_action(query: str) -> bool:
    q = query.lower()
    verbs = ("напиши", "отправь", "send", "message", "сообщение")
    return any(v in q for v in verbs)


class TaskStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: dict[str, TaskResponse] = {}

    def create_task(self, req: TaskCreateRequest) -> TaskResponse:
        task_id = str(uuid4())
        trace_id = str(uuid4())

        trace = [
            TraceItem(
                step="task_created",
                status="ok",
                ts=datetime.now(timezone.utc),
            )
        ]

        # MVP stub result so frontend can integrate immediately.
        result = TaskResult(
            product={"title": "pending", "url": "", "price": None},
            news=[],
            sources=[],
            actions=[],
        )

        if req.allow_social_actions and requires_message_action(req.query):
            result.actions.append(
                ActionItem(
                    action_id=str(uuid4()),
                    type="message_send",
                    status="waiting_confirm",
                    payload={
                        "destination_hint": req.query,
                        "message_text": req.query,
                    },
                )
            )
            status = "needs_confirmation"
        else:
            status = "running"

        task = TaskResponse(
            task_id=task_id,
            trace_id=trace_id,
            status=status,
            result=result,
            trace=trace,
            error=None,
        )

        with self._lock:
            self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> TaskResponse | None:
        with self._lock:
            return self._tasks.get(task_id)

    def confirm_action(self, req: ActionConfirmRequest) -> ActionItem | None:
        with self._lock:
            task = self._tasks.get(req.task_id)
            if task is None or task.result is None:
                return None

            action = next((a for a in task.result.actions if a.action_id == req.action_id), None)
            if action is None:
                return None

            if req.decision == "approve":
                action.status = "sent"
                task.status = "running"
                task.trace.append(
                    TraceItem(
                        step="action_confirmed",
                        status="ok",
                        ts=datetime.now(timezone.utc),
                        tool="browser.message.send",
                    )
                )
            else:
                action.status = "cancelled"
                task.status = "failed"
                task.error = "Action rejected by user"
                task.trace.append(
                    TraceItem(
                        step="action_rejected",
                        status="cancelled",
                        ts=datetime.now(timezone.utc),
                    )
                )

            return action
