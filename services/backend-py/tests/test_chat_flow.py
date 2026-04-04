from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app, store


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


@pytest.fixture()
def client() -> TestClient:
    with store._lock:
        store._tasks.clear()
        store._conversations.clear()
        store._messages.clear()
        store._tools = FakeToolsClient()
    return TestClient(app)


def test_task_has_session_id(client: TestClient) -> None:
    resp = client.post(
        "/task",
        json={"query": "Найди iPhone 256", "allow_social_actions": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
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
    assert task_after["result"]["actions"][0]["status"] == "cancelled"

    messages = client.get(f"/chat/conversations/{cid}/messages").json()["items"]
    assistant = [m for m in messages if m["role"] == "assistant" and m["task_id"] == task["task_id"]][0]
    assert "отменено пользователем" in assistant["content"].lower()
