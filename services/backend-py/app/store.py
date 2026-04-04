from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from .schemas import (
    ActionConfirmRequest,
    ActionItem,
    ConversationMessageCreateRequest,
    ConversationMessageCreateResponse,
    ConversationResponse,
    MessageItem,
    TaskCreateRequest,
    TaskResponse,
    TaskResult,
    TraceItem,
)
from .tools_client import ToolsClient, ToolsClientError


def requires_message_action(query: str) -> bool:
    q = query.lower()
    verbs = ("напиши", "отправь", "send", "message", "сообщение")
    return any(v in q for v in verbs)


class TaskStore:
    def __init__(self, tools_client: ToolsClient | None = None) -> None:
        self._lock = Lock()
        self._tasks: dict[str, TaskResponse] = {}
        self._conversations: dict[str, ConversationResponse] = {}
        self._messages: dict[str, list[MessageItem]] = {}
        self._tools = tools_client

    def _append_trace(
        self,
        trace: list[TraceItem],
        step: str,
        status: str,
        tool: str | None = None,
    ) -> None:
        trace.append(
            TraceItem(
                step=step,
                status=status,
                tool=tool,
                ts=datetime.now(timezone.utc),
            )
        )

    def _call_tool(
        self,
        trace: list[TraceItem],
        trace_id: str,
        tool: str,
        session_id: str | None,
        input_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._tools is None:
            self._append_trace(trace, f"{tool}_skipped", "no_client", tool)
            return None

        try:
            resp = self._tools.call_tool(
                tool=tool,
                session_id=session_id,
                input_data=input_data,
                trace_id=trace_id,
            )
            self._append_trace(trace, f"{tool}_ok", "ok", tool)
            return resp
        except ToolsClientError:
            self._append_trace(trace, f"{tool}_failed", "fallback", tool)
            return None

    def create_task(self, req: TaskCreateRequest, conversation_id: str | None = None) -> TaskResponse:
        task_id = str(uuid4())
        trace_id = str(uuid4())
        session_id: str | None = None

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

        status = "running"

        # Universal product/news retrieval pipeline.
        search_resp = self._call_tool(
            trace=trace,
            trace_id=trace_id,
            tool="browser.search",
            session_id=session_id,
            input_data={"query": req.query, "engine": "yandex", "limit": 5},
        )
        if search_resp and isinstance(search_resp, dict):
            session_id = search_resp.get("session_id") or session_id

        extract_product_resp = self._call_tool(
            trace=trace,
            trace_id=trace_id,
            tool="browser.extract",
            session_id=session_id,
            input_data={
                "schema": {
                    "type": "product",
                    "fields": ["title", "price", "currency", "url"],
                },
                "mode": "dom_first",
                "limit": 5,
            },
        )
        product_items = (
            extract_product_resp.get("output", {}).get("items", [])
            if isinstance(extract_product_resp, dict)
            else []
        )
        if product_items:
            result.product = product_items[0]
        else:
            result.product = {"title": "pending", "url": "", "price": None}

        extract_news_resp = self._call_tool(
            trace=trace,
            trace_id=trace_id,
            tool="browser.extract",
            session_id=session_id,
            input_data={
                "schema": {
                    "type": "news",
                    "fields": ["title", "summary", "published_at", "url", "source"],
                },
                "mode": "dom_first",
                "limit": 5,
            },
        )
        news_items = (
            extract_news_resp.get("output", {}).get("items", [])
            if isinstance(extract_news_resp, dict)
            else []
        )
        if isinstance(news_items, list):
            result.news = news_items

        if isinstance(search_resp, dict):
            results = search_resp.get("output", {}).get("results", [])
            if isinstance(results, list):
                result.sources = [item.get("url", "") for item in results if isinstance(item, dict)]

        if req.allow_social_actions and requires_message_action(req.query):
            draft_resp = self._call_tool(
                trace=trace,
                trace_id=trace_id,
                tool="browser.message.draft",
                session_id=session_id,
                input_data={
                    "destination_hint": req.query,
                    "message_text": req.query,
                },
            )

            if isinstance(draft_resp, dict):
                draft_output = draft_resp.get("output", {})
                action_id = draft_output.get("action_id", str(uuid4()))
                payload = draft_output if isinstance(draft_output, dict) else {}
                if draft_resp.get("session_id") is not None:
                    payload["session_id"] = draft_resp.get("session_id")
            else:
                action_id = str(uuid4())
                payload = {
                    "destination_hint": req.query,
                    "message_text": req.query,
                }

            result.actions.append(
                ActionItem(
                    action_id=action_id,
                    type="message_send",
                    status="waiting_confirm",
                    payload=payload,
                )
            )
            status = "needs_confirmation"

        task = TaskResponse(
            task_id=task_id,
            trace_id=trace_id,
            status=status,
            conversation_id=conversation_id,
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
                session_id = None
                if isinstance(action.payload, dict):
                    maybe_session = action.payload.get("session_id")
                    if isinstance(maybe_session, str):
                        session_id = maybe_session

                send_resp = self._call_tool(
                    trace=task.trace,
                    trace_id=task.trace_id,
                    tool="browser.message.send",
                    session_id=session_id,
                    input_data={"action_id": req.action_id, "confirm": True},
                )
                if isinstance(send_resp, dict) and send_resp.get("ok", True):
                    action.status = "sent"
                    task.status = "running"
                    self._append_trace(
                        task.trace,
                        "action_confirmed",
                        "ok",
                        "browser.message.send",
                    )
                else:
                    action.status = "failed"
                    task.status = "failed"
                    task.error = "Message send failed"
                    self._append_trace(
                        task.trace,
                        "action_confirmed",
                        "failed",
                        "browser.message.send",
                    )
            else:
                action.status = "cancelled"
                task.status = "failed"
                task.error = "Action rejected by user"
                self._append_trace(task.trace, "action_rejected", "cancelled")

            return action

    def create_conversation(self, title: str | None = None) -> ConversationResponse:
        now = datetime.now(timezone.utc)
        conversation_id = str(uuid4())
        conv = ConversationResponse(
            conversation_id=conversation_id,
            title=title or "Новый чат",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conversations[conversation_id] = conv
            self._messages[conversation_id] = []
        return conv

    def get_conversation(self, conversation_id: str) -> ConversationResponse | None:
        with self._lock:
            return self._conversations.get(conversation_id)

    def list_conversations(self) -> list[ConversationResponse]:
        with self._lock:
            values = list(self._conversations.values())
        return sorted(values, key=lambda x: x.updated_at, reverse=True)

    def list_messages(self, conversation_id: str) -> list[MessageItem] | None:
        with self._lock:
            items = self._messages.get(conversation_id)
            if items is None:
                return None
            return list(items)

    def _build_assistant_text(self, task: TaskResponse) -> str:
        if task.status == "needs_confirmation":
            return "Требуется подтверждение действия перед отправкой сообщения."

        product_title = ""
        if task.result and isinstance(task.result.product, dict):
            product_title = str(task.result.product.get("title", "")).strip()

        news_count = len(task.result.news) if task.result else 0
        parts: list[str] = []
        if product_title:
            parts.append(f"Товар: {product_title}")
        if news_count:
            parts.append(f"Новости: {news_count}")
        if not parts:
            return "Задача принята в обработку."
        return ". ".join(parts)

    def add_message_and_create_task(
        self,
        conversation_id: str,
        req: ConversationMessageCreateRequest,
    ) -> ConversationMessageCreateResponse | None:
        with self._lock:
            conv = self._conversations.get(conversation_id)
            if conv is None:
                return None

        user_message = MessageItem(
            message_id=str(uuid4()),
            conversation_id=conversation_id,
            role="user",
            content=req.content,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._messages[conversation_id].append(user_message)

        task = self.create_task(
            TaskCreateRequest(query=req.content, allow_social_actions=req.allow_social_actions),
            conversation_id=conversation_id,
        )

        assistant_message = MessageItem(
            message_id=str(uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content=self._build_assistant_text(task),
            created_at=datetime.now(timezone.utc),
            task_id=task.task_id,
        )
        with self._lock:
            self._messages[conversation_id].append(assistant_message)
            conv = self._conversations[conversation_id]
            if conv.title == "Новый чат":
                conv.title = req.content[:60]
            conv.updated_at = datetime.now(timezone.utc)
            self._conversations[conversation_id] = conv

        return ConversationMessageCreateResponse(
            conversation_id=conversation_id,
            user_message=user_message,
            assistant_message=assistant_message,
            task=task,
        )
