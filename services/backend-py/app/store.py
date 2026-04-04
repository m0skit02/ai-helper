from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from .llm_client import LLMClient, LLMClientError
from .schemas import (
    ActionConfirmRequest,
    ActionItem,
    ConversationMessageCreateRequest,
    ConversationMessageCreateResponse,
    ConversationResponse,
    MessageItem,
    NewsItem,
    ProductItem,
    TaskCreateRequest,
    TaskResponse,
    TaskResult,
    TraceItem,
)
from .tools_client import ToolsClient, ToolsClientError


def requires_message_action(query: str) -> bool:
    q = query.lower()
    patterns = (
        "напиши",
        "отправь",
        "send",
        "write to",
        "отправь сообщение",
        "напиши сообщение",
    )
    return any(pattern in q for pattern in patterns)


def wants_news_search(query: str) -> bool:
    q = query.lower()
    patterns = ("новост", "news", "стат", "публикац", "за неделю", "за день")
    return any(pattern in q for pattern in patterns)


def wants_product_search(query: str) -> bool:
    q = query.lower()
    keywords = (
        "iphone",
        "samsung",
        "xiaomi",
        "товар",
        "маркетплейс",
        "купить",
        "цена",
        "стоимость",
        "памят",
        "гб",
        "gb",
        "доставка",
        "продавец",
        "новый",
    )
    return any(keyword in q for keyword in keywords)


class TaskStore:
    def __init__(
        self,
        tools_client: ToolsClient | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._lock = Lock()
        self._tasks: dict[str, TaskResponse] = {}
        self._conversations: dict[str, ConversationResponse] = {}
        self._messages: dict[str, list[MessageItem]] = {}
        self._tools = tools_client
        self._llm = llm_client

    def _empty_result(self) -> TaskResult:
        return TaskResult(
            product=None,
            news=[],
            sources=[],
            actions=[],
        )

    def reset_for_tests(
        self,
        tools_client: ToolsClient | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        with self._lock:
            self._tasks.clear()
            self._conversations.clear()
            self._messages.clear()
            self._tools = tools_client
            self._llm = llm_client

    def _append_trace(
        self,
        trace: list[TraceItem],
        step: str,
        status: str,
        tool: str | None = None,
        detail: str | None = None,
    ) -> None:
        trace.append(
            TraceItem(
                step=step,
                status=status,
                tool=tool,
                detail=detail,
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
        except ToolsClientError as exc:
            self._append_trace(
                trace,
                f"{tool}_failed",
                "fallback",
                tool,
                detail=f"{exc.category}: {exc.message}",
            )
            return None

    def _set_assistant_message_for_task(
        self,
        conversation_id: str | None,
        task_id: str,
        content: str,
    ) -> None:
        if conversation_id is None:
            return
        messages = self._messages.get(conversation_id)
        if not messages:
            return
        now = datetime.now(timezone.utc)
        for message in reversed(messages):
            if message.role == "assistant" and message.task_id == task_id:
                message.content = content
                message.created_at = now
                break
        conv = self._conversations.get(conversation_id)
        if conv is not None:
            conv.updated_at = now
            self._conversations[conversation_id] = conv

    def _normalize_product(self, payload: dict[str, Any] | None) -> ProductItem:
        p = payload or {}

        def as_float(value: Any) -> float | None:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def as_int(value: Any) -> int | None:
            if value is None:
                return None
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

        return ProductItem(
            title=str(p.get("title", "")),
            price=as_float(p.get("price")),
            currency=(str(p["currency"]) if p.get("currency") is not None else None),
            url=str(p.get("url", "")),
            seller=(str(p["seller"]) if p.get("seller") is not None else None),
            rating=as_float(p.get("rating")),
            reviews_count=as_int(p.get("reviews_count")),
            delivery=(str(p["delivery"]) if p.get("delivery") is not None else None),
            condition=(str(p["condition"]) if p.get("condition") is not None else None),
            storage_gb=as_int(p.get("storage_gb")),
        )

    def _normalize_news(self, items: Any) -> list[NewsItem]:
        if not isinstance(items, list):
            return []
        normalized: list[NewsItem] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            normalized.append(
                NewsItem(
                    title=str(raw.get("title", "")),
                    summary=str(raw.get("summary", "")),
                    published_at=(str(raw["published_at"]) if raw.get("published_at") is not None else None),
                    url=str(raw.get("url", "")),
                    source=(str(raw["source"]) if raw.get("source") is not None else None),
                )
            )
        return normalized

    def _normalize_sources(self, items: Any) -> list[str]:
        if not isinstance(items, list):
            return []
        dedup: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            dedup.append(url)
        return dedup

    def _has_product_data(self, product: ProductItem | None) -> bool:
        if product is None:
            return False
        title = product.title.strip().lower()
        return bool(
            (title and title != "pending")
            or product.price is not None
            or product.url.strip()
        )

    def _merge_sources(self, *groups: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                url = item.strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                merged.append(url)
        return merged

    def _build_link_entries(self, task: TaskResponse) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add_entry(label: str, url: str) -> None:
            clean_label = label.strip()
            clean_url = url.strip()
            if not clean_url or clean_url in seen:
                return
            seen.add(clean_url)
            entries.append((clean_label or clean_url, clean_url))

        if task.result is None:
            return entries

        if self._has_product_data(task.result.product) and task.result.product is not None:
            add_entry(task.result.product.title or "Товар", task.result.product.url)

        for item in task.result.news:
            add_entry(item.title or "Новость", item.url)

        for url in task.result.sources:
            add_entry("Источник", url)

        return entries

    def _format_links_block(self, task: TaskResponse) -> str:
        entries = self._build_link_entries(task)
        if not entries:
            return ""

        lines = ["Ссылки:"]
        for index, (label, url) in enumerate(entries[:5], start=1):
            lines.append(f"{index}. {label}")
            lines.append(url)
        return "\n".join(lines)

    def _build_fallback_summary(self, task: TaskResponse) -> str:
        if task.status == "failed":
            return "Не удалось выполнить задачу."

        parts: list[str] = []
        if task.result is not None and self._has_product_data(task.result.product) and task.result.product is not None:
            product = task.result.product
            product_line = f"Нашел товар: {product.title}"
            if product.price is not None:
                currency = f" {product.currency}" if product.currency else ""
                product_line += f" за {product.price:g}{currency}"
            parts.append(product_line + ".")

        if task.result is not None and task.result.news:
            news_titles = [item.title.strip() for item in task.result.news if item.title.strip()]
            if news_titles:
                preview = "; ".join(news_titles[:3])
                parts.append(f"Нашел новости: {preview}.")

        if not parts:
            if task.status == "needs_confirmation":
                return "Подготовил действие. Нужна ваша команда на подтверждение."
            if task.status == "done":
                return "Не удалось получить содержательные результаты по запросу."
            return "Задача принята в обработку."

        return " ".join(parts)

    def _answer_general_query(self, query: str, trace: list[TraceItem]) -> str:
        if self._llm is not None and self._llm.enabled():
            try:
                answer = self._llm.answer_query(query)
                self._append_trace(trace, "llm_answer_ok", "ok", "llm.answer")
                return answer
            except LLMClientError as exc:
                self._append_trace(
                    trace,
                    "llm_answer_failed",
                    "fallback",
                    "llm.answer",
                    detail=f"{exc.category}: {exc.message}",
                )

        return "Не удалось подготовить ответ по этому запросу."

    def _decorate_assistant_text(self, summary: str, task: TaskResponse) -> str:
        sections = [summary.strip()]
        links_block = self._format_links_block(task)
        if links_block and "http://" not in summary and "https://" not in summary:
            sections.append(links_block)

        if task.status == "needs_confirmation":
            sections.append("Подготовил сообщение. Подтвердите отправку.")
        return "\n\n".join(part for part in sections if part)

    def _fallback_plan(self, query: str) -> dict[str, Any]:
        wants_message = requires_message_action(query)
        wants_news = wants_news_search(query)
        wants_product = wants_product_search(query)
        return {
            "wants_product": wants_product,
            "wants_news": wants_news,
            "wants_message": wants_message,
            "search_query": query,
            "news_topic": query,
            "destination_hint": query,
            "message_text": query,
        }

    def _plan_query(self, query: str, trace: list[TraceItem]) -> dict[str, Any]:
        fallback = self._fallback_plan(query)
        has_supported_work = bool(
            fallback["wants_product"] or fallback["wants_news"] or fallback["wants_message"]
        )
        if has_supported_work:
            self._append_trace(trace, "rule_plan_ok", "ok", "rule.plan")
        else:
            self._append_trace(trace, "rule_plan_general", "ok", "rule.plan")
        return fallback

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
        plan = self._plan_query(req.query, trace)
        has_supported_work = bool(plan["wants_product"] or plan["wants_news"] or plan["wants_message"])
        has_search_work = bool(plan["wants_product"] or plan["wants_news"])

        result = self._empty_result()

        status = "running"
        task_error: str | None = None

        if not has_supported_work:
            status = "done"
            task = TaskResponse(
                task_id=task_id,
                trace_id=trace_id,
                status=status,
                conversation_id=conversation_id,
                session_id=session_id,
                result=result,
                trace=trace,
                error=None,
            )
            with self._lock:
                self._tasks[task_id] = task
            return task

        # Universal product/news retrieval pipeline.
        search_resp = None
        if has_search_work:
            search_resp = self._call_tool(
                trace=trace,
                trace_id=trace_id,
                tool="browser.search",
                session_id=session_id,
                input_data={"query": plan["search_query"], "engine": "yandex", "limit": 5},
            )
            if search_resp and isinstance(search_resp, dict):
                session_id = search_resp.get("session_id") or session_id

        extract_product_resp = None
        if plan["wants_product"]:
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
        if isinstance(product_items, list) and product_items:
            first = product_items[0] if isinstance(product_items[0], dict) else {}
            result.product = self._normalize_product(first)

        extract_news_resp = None
        if plan["wants_news"]:
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
        result.news = self._normalize_news(news_items)

        search_sources: list[str] = []
        if isinstance(search_resp, dict):
            results = search_resp.get("output", {}).get("results", [])
            search_sources = self._normalize_sources(results)

        news_sources = [item.url for item in result.news if item.url.strip()]
        product_sources = [result.product.url] if self._has_product_data(result.product) and result.product is not None else []
        result.sources = self._merge_sources(product_sources, news_sources, search_sources)

        if req.allow_social_actions and bool(plan["wants_message"]):
            draft_resp = self._call_tool(
                trace=trace,
                trace_id=trace_id,
                tool="browser.message.draft",
                session_id=session_id,
                input_data={
                    "destination_hint": plan["destination_hint"],
                    "message_text": plan["message_text"],
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
                    "destination_hint": plan["destination_hint"],
                    "message_text": plan["message_text"],
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
        else:
            status = "done"

        task = TaskResponse(
            task_id=task_id,
            trace_id=trace_id,
            status=status,
            conversation_id=conversation_id,
            session_id=session_id,
            result=result,
            trace=trace,
            error=task_error,
        )

        with self._lock:
            self._tasks[task_id] = task
        return task

    def create_task_shell(self, req: TaskCreateRequest, conversation_id: str | None = None) -> TaskResponse:
        task = TaskResponse(
            task_id=str(uuid4()),
            trace_id=str(uuid4()),
            status="running",
            conversation_id=conversation_id,
            session_id=None,
            result=self._empty_result(),
            trace=[
                TraceItem(
                    step="task_created",
                    status="ok",
                    ts=datetime.now(timezone.utc),
                )
            ],
            error=None,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        return task

    def process_task(self, task_id: str, req: TaskCreateRequest) -> TaskResponse | None:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None

        task = task.model_copy(deep=True)
        trace = task.trace
        trace_id = task.trace_id
        session_id = task.session_id
        conversation_id = task.conversation_id
        plan = self._plan_query(req.query, trace)
        has_supported_work = bool(plan["wants_product"] or plan["wants_news"] or plan["wants_message"])
        has_search_work = bool(plan["wants_product"] or plan["wants_news"])
        result = self._empty_result()
        task_error: str | None = None
        status = "running"

        if not has_supported_work:
            assistant_text = self._answer_general_query(req.query, trace)
            task.status = "done"
            task.result = result
            task.error = None
            task.trace = trace
            if conversation_id is not None:
                self._set_assistant_message_for_task(
                    conversation_id,
                    task.task_id,
                    assistant_text,
                )
            with self._lock:
                self._tasks[task_id] = task
            return task

        search_resp = None
        if has_search_work:
            search_resp = self._call_tool(
                trace=trace,
                trace_id=trace_id,
                tool="browser.search",
                session_id=session_id,
                input_data={"query": plan["search_query"], "engine": "yandex", "limit": 5},
            )
            if search_resp and isinstance(search_resp, dict):
                session_id = search_resp.get("session_id") or session_id

        extract_product_resp = None
        if plan["wants_product"]:
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
        if isinstance(product_items, list) and product_items:
            first = product_items[0] if isinstance(product_items[0], dict) else {}
            result.product = self._normalize_product(first)

        extract_news_resp = None
        if plan["wants_news"]:
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
        result.news = self._normalize_news(news_items)

        search_sources: list[str] = []
        if isinstance(search_resp, dict):
            results = search_resp.get("output", {}).get("results", [])
            search_sources = self._normalize_sources(results)

        news_sources = [item.url for item in result.news if item.url.strip()]
        product_sources = [result.product.url] if self._has_product_data(result.product) and result.product is not None else []
        result.sources = self._merge_sources(product_sources, news_sources, search_sources)

        if req.allow_social_actions and bool(plan["wants_message"]):
            draft_resp = self._call_tool(
                trace=trace,
                trace_id=trace_id,
                tool="browser.message.draft",
                session_id=session_id,
                input_data={
                    "destination_hint": plan["destination_hint"],
                    "message_text": plan["message_text"],
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
                    "destination_hint": plan["destination_hint"],
                    "message_text": plan["message_text"],
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
        else:
            status = "done"

        task.status = status
        task.session_id = session_id
        task.result = result
        task.trace = trace
        task.error = task_error
        assistant_text: str | None = None

        if conversation_id is not None:
            assistant_text = self._build_assistant_text(req.query, task)
            self._set_assistant_message_for_task(
                conversation_id,
                task.task_id,
                assistant_text,
            )

        with self._lock:
            self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> TaskResponse | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task is not None else None

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
                    task.status = "done"
                    task.error = None
                    self._set_assistant_message_for_task(
                        task.conversation_id,
                        task.task_id,
                        "Действие выполнено: сообщение отправлено.",
                    )
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
                    self._set_assistant_message_for_task(
                        task.conversation_id,
                        task.task_id,
                        "Не удалось отправить сообщение. Повторите позже.",
                    )
                    self._append_trace(
                        task.trace,
                        "action_confirmed",
                        "failed",
                        "browser.message.send",
                    )
            else:
                action.status = "cancelled"
                task.status = "done"
                task.error = None
                self._set_assistant_message_for_task(
                    task.conversation_id,
                    task.task_id,
                    "Действие отменено пользователем.",
                )
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
            conv = self._conversations.get(conversation_id)
            return conv.model_copy(deep=True) if conv is not None else None

    def list_conversations(self) -> list[ConversationResponse]:
        with self._lock:
            values = [item.model_copy(deep=True) for item in self._conversations.values()]
        return sorted(values, key=lambda x: x.updated_at, reverse=True)

    def list_messages(self, conversation_id: str) -> list[MessageItem] | None:
        with self._lock:
            items = self._messages.get(conversation_id)
            if items is None:
                return None
            return [item.model_copy(deep=True) for item in items]

    def _build_assistant_text(self, query: str, task: TaskResponse) -> str:
        if self._llm is not None and self._llm.enabled():
            try:
                payload = task.model_dump(mode="json")
                summary = self._llm.summarize_task(
                    query=query,
                    task_status=task.status,
                    result=payload.get("result", {}),
                )
                return self._decorate_assistant_text(summary, task)
            except LLMClientError:
                pass

        return self._decorate_assistant_text(self._build_fallback_summary(task), task)

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

        task = self.create_task_shell(
            TaskCreateRequest(query=req.content, allow_social_actions=req.allow_social_actions),
            conversation_id=conversation_id,
        )

        assistant_message = MessageItem(
            message_id=str(uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content="Задача принята в обработку.",
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
