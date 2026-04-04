from __future__ import annotations

import time
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

    def summarize_task(self, query: str, task_status: str, result: dict[str, Any]) -> str:
        return "stub summary"

    def answer_query(self, query: str) -> str:
        return "Для набора мышечной массы нужен профицит калорий, достаточный белок и силовые тренировки."


class FakeLLMClientFail:
    def enabled(self) -> bool:
        return True

    def summarize_task(self, query: str, task_status: str, result: dict[str, Any]) -> str:
        raise LLMClientError("auth", "http_401: invalid api key")


class FakeLLMClientGeneralAnswer:
    def enabled(self) -> bool:
        return True

    def summarize_task(self, query: str, task_status: str, result: dict[str, Any]) -> str:
        return "stub summary"

    def answer_query(self, query: str) -> str:
        return "Для набора мышечной массы нужен профицит калорий, достаточный белок и силовые тренировки."


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


class FakeToolsClientMessageOnly(FakeToolsClient):
    def call_tool(
        self,
        tool: str,
        session_id: str | None,
        input_data: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        if tool == "browser.search":
            raise AssertionError("browser.search should not be called for message-only requests")
        if tool == "browser.extract":
            raise AssertionError("browser.extract should not be called for message-only requests")
        return super().call_tool(tool=tool, session_id=session_id, input_data=input_data, trace_id=trace_id)


@pytest.fixture()
def client() -> TestClient:
    store.reset_for_tests(tools_client=FakeToolsClient())
    return TestClient(app)


def wait_for_task(client: TestClient, task_id: str, *, timeout_s: float = 2.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        resp = client.get(f"/task/{task_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] != "running":
            return last
        time.sleep(0.05)
    assert last is not None
    return last


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
    task = wait_for_task(client, created_data["task"]["task_id"])
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
    task = wait_for_task(client, created["task"]["task_id"])
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


def test_rule_plan_trace_for_supported_request(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClient(), llm_client=FakeLLMClientOk())

    resp = client.post(
        "/task",
        json={"query": "Найди новости OpenAI и отправь сообщение Сергею", "allow_social_actions": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    rule_trace = [item for item in data["trace"] if item["step"] == "rule_plan_ok"]
    assert rule_trace
    assert data["status"] == "needs_confirmation"


def test_general_query_uses_general_rule_path(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClient(), llm_client=FakeLLMClientFail())

    resp = client.post(
        "/task",
        json={"query": "Как зовут Трампа", "allow_social_actions": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    step = [item for item in data["trace"] if item["step"] == "rule_plan_general"][0]
    assert step["tool"] == "rule.plan"
    assert [item for item in data["trace"] if "browser.search" in item["step"]] == []


def test_assistant_summary_contains_links_and_confirmation_note(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClient(), llm_client=FakeLLMClientOk())

    conv = client.post("/chat/conversations", json={"title": "Links"}).json()
    cid = conv["conversation_id"]

    created = client.post(
        f"/chat/conversations/{cid}/messages",
        json={"content": "Найди новости OpenAI и отправь сообщение Сергею", "allow_social_actions": True},
    )

    assert created.status_code == 200
    task_id = created.json()["task"]["task_id"]
    wait_for_task(client, task_id)
    messages = client.get(f"/chat/conversations/{cid}/messages").json()["items"]
    assistant_message = [m for m in messages if m["role"] == "assistant" and m["task_id"] == task_id][0]["content"]
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


def test_general_query_returns_llm_answer_in_chat(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClient(), llm_client=FakeLLMClientGeneralAnswer())

    conv = client.post("/chat/conversations", json={"title": "General"}).json()
    cid = conv["conversation_id"]

    created = client.post(
        f"/chat/conversations/{cid}/messages",
        json={"content": "Что нужно чтобы набрать мышечную массу", "allow_social_actions": True},
    )

    assert created.status_code == 200
    task_id = created.json()["task"]["task_id"]
    task = wait_for_task(client, task_id)
    assert task["status"] == "done"
    assert task["error"] is None

    messages = client.get(f"/chat/conversations/{cid}/messages").json()["items"]
    assistant_message = [m for m in messages if m["role"] == "assistant" and m["task_id"] == task_id][0]["content"]
    assert "профицит калорий" in assistant_message.lower()


def test_message_only_request_skips_search_and_sources(client: TestClient) -> None:
    store.reset_for_tests(tools_client=FakeToolsClientMessageOnly(), llm_client=None)

    resp = client.post(
        "/task",
        json={"query": "Отправь сообщение Сергею: привет", "allow_social_actions": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "needs_confirmation"
    assert data["result"]["sources"] == []
    assert [item["step"] for item in data["trace"] if "browser.search" in item["step"]] == []
