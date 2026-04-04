from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.llm_client import LLMClientError
from app.main import app, store
from app.tools_client import ToolsClientError


class FakeToolsClient:
    def call_tool(
        self,
        tool: str,
        session_id: str | None,
        input_data: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        if tool == "browser.search":
            return {
                "trace_id": trace_id,
                "session_id": "sess-1",
                "tool": tool,
                "ok": True,
                "output": {
                    "results": [
                        {"title": "item", "url": "https://example.com/item", "snippet": "stub"},
                    ]
                },
                "error": None,
                "duration_ms": 10,
            }

        if tool == "browser.extract":
            schema_type = input_data.get("schema", {}).get("type")
            if schema_type == "product":
                return {
                    "trace_id": trace_id,
                    "session_id": "sess-1",
                    "tool": tool,
                    "ok": True,
                    "output": {
                        "items": [
                            {
                                "title": "iPhone 256",
                                "price": 1000,
                                "currency": "RUB",
                                "url": "https://example.com/iphone",
                            }
                        ]
                    },
                    "error": None,
                    "duration_ms": 10,
                }
            return {
                "trace_id": trace_id,
                "session_id": "sess-1",
                "tool": tool,
                "ok": True,
                "output": {
                    "items": [
                        {
                            "title": "Apple news",
                            "summary": "stub",
                            "published_at": "2026-04-04T00:00:00Z",
                            "url": "https://example.com/news",
                            "source": "example",
                        }
                    ]
                },
                "error": None,
                "duration_ms": 10,
            }

        if tool == "browser.message.draft":
            return {
                "trace_id": trace_id,
                "session_id": "sess-1",
                "tool": tool,
                "ok": True,
                "output": {
                    "action_id": "act-1",
                    "destination_hint": input_data.get("destination_hint", ""),
                    "message_text": input_data.get("message_text", ""),
                },
                "error": None,
                "duration_ms": 10,
            }

        if tool == "browser.message.send":
            return {
                "trace_id": trace_id,
                "session_id": session_id or "sess-1",
                "tool": tool,
                "ok": True,
                "output": {"status": "sent"},
                "error": None,
                "duration_ms": 10,
            }

        raise AssertionError(f"unexpected tool {tool}")


class FakeLLMClientOk:
    def enabled(self) -> bool:
        return True

    def plan_query(self, query: str) -> dict[str, Any]:
        return {
            "wants_product": False,
            "wants_news": True,
            "wants_message": True,
            "search_query": "openai news",
            "news_topic": "OpenAI",
            "destination_hint": "Сергей",
            "message_text": "Короткая сводка по OpenAI.",
        }

    def summarize_task(self, query: str, task_status: str, result: dict[str, Any]) -> str:
        return "stub summary"


class FakeLLMClientFail:
    def enabled(self) -> bool:
        return True

    def plan_query(self, query: str) -> dict[str, Any]:
        raise LLMClientError("auth", "http_401: invalid api key")

    def summarize_task(self, query: str, task_status: str, result: dict[str, Any]) -> str:
        raise LLMClientError("auth", "http_401: invalid api key")


class FakeToolsClientSearchFail(FakeToolsClient):
    def call_tool(
        self,
        tool: str,
        session_id: str | None,
        input_data: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        if tool == "browser.search":
            raise ToolsClientError("network", "connection refused")
        return super().call_tool(tool=tool, session_id=session_id, input_data=input_data, trace_id=trace_id)


@pytest.fixture()
def client() -> TestClient:
    store.reset_for_tests(tools_client=FakeToolsClient())
    return TestClient(app)


def test_task_has_session_id(client: TestClient) -> None:
    resp = client.post(
        "/task",
        json={"query": "Найди iPhone 256", "allow_social_actions": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["session_id"] == "sess-1"


def test_confirm_approve_updates_assistant_message(client: TestClient) -> None:
    conv = client.post("/chat/conversations", json={"title": "T1"}).json()
    cid = conv["conversation_id"]

    created = client.post(
        f"/chat/conversations/{cid}/messages",
        json={"content": "Отправь сообщение Сергею", "allow_social_actions": True},
    )
    assert created.status_code == 200
    created_data = created.json()
    task = created_data["task"]
    action_id = task["result"]["actions"][0]["action_id"]

    confirm = client.post(
        "/action/confirm",
        json={"task_id": task["task_id"], "action_id": action_id, "decision": "approve"},
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "sent"

    task_after = client.get(f"/task/{task['task_id']}").json()
    assert task_after["status"] == "done"
    assert task_after["result"]["actions"][0]["status"] == "sent"

    messages = client.get(f"/chat/conversations/{cid}/messages").json()["items"]
    assistant = [m for m in messages if m["role"] == "assistant" and m["task_id"] == task["task_id"]][0]
    assert "сообщение отправлено" in assistant["content"].lower()


def test_confirm_reject_updates_assistant_message(client: TestClient) -> None:
    conv = client.post("/chat/conversations", json={"title": "T2"}).json()
    cid = conv["conversation_id"]

    created = client.post(
        f"/chat/conversations/{cid}/messages",
        json={"content": "Отправь сообщение", "allow_social_actions": True},
    ).json()
    task = created["task"]
    action_id = task["result"]["actions"][0]["action_id"]

    confirm = client.post(
        "/action/confirm",
        json={"task_id": task["task_id"], "action_id": action_id, "decision": "reject"},
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "cancelled"

    task_after = client.get(f"/task/{task['task_id']}").json()
    assert task_after["status"] == "done"
    assert task_after["result"]["actions"][0]["status"] == "cancelled"

    messages = client.get(f"/chat/conversations/{cid}/messages").json()["items"]
    assistant = [m for m in messages if m["role"] == "assistant" and m["task_id"] == task["task_id"]][0]
    assert "отменено пользователем" in assistant["content"].lower()


def test_llm_plan_success_trace(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClient(), llm_client=FakeLLMClientOk())

    resp = client.post(
        "/task",
        json={"query": "Найди новости OpenAI и отправь сообщение Сергею", "allow_social_actions": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    llm_trace = [item for item in data["trace"] if item["step"] == "llm_plan_ok"]
    assert llm_trace
    assert data["status"] == "needs_confirmation"


def test_llm_plan_failure_trace_contains_reason(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClient(), llm_client=FakeLLMClientFail())

    resp = client.post(
        "/task",
        json={"query": "Найди новости OpenAI", "allow_social_actions": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    failed = [item for item in data["trace"] if item["step"] == "llm_plan_failed"][0]
    assert failed["detail"] == "auth: http_401: invalid api key"


def test_assistant_summary_contains_links_and_confirmation_note(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClient(), llm_client=FakeLLMClientOk())

    conv = client.post("/chat/conversations", json={"title": "Links"}).json()
    cid = conv["conversation_id"]

    created = client.post(
        f"/chat/conversations/{cid}/messages",
        json={"content": "Найди новости OpenAI и отправь сообщение Сергею", "allow_social_actions": True},
    )

    assert created.status_code == 200
    assistant_message = created.json()["assistant_message"]["content"]
    assert "stub summary" in assistant_message
    assert "Ссылки:" in assistant_message
    assert "https://example.com/news" in assistant_message
    assert "Подтвердите отправку" in assistant_message


def test_tool_failure_trace_contains_reason(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClientSearchFail(), llm_client=None)

    resp = client.post(
        "/task",
        json={"query": "Найди новости OpenAI", "allow_social_actions": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    failed = [item for item in data["trace"] if item["step"] == "browser.search_failed"][0]
    assert failed["detail"] == "network: connection refused"
