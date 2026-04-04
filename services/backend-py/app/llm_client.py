from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


class LLMClientError(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-5")
        self.url = os.getenv("OPENAI_URL", "https://api.openai.com/v1/chat/completions")
        self.timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))

    def enabled(self) -> bool:
        return bool(self.api_key)

    def _chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.enabled():
            raise LLMClientError("OPENAI_API_KEY is not set")

        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except error.URLError as exc:
            raise LLMClientError(str(exc)) from exc

        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMClientError("Invalid LLM response") from exc

    def plan_query(self, query: str) -> dict[str, Any]:
        system_prompt = (
            "You are an assistant planner for a browser agent. "
            "Return strict JSON with fields: "
            "wants_product(bool), wants_news(bool), wants_message(bool), "
            "search_query(str), news_topic(str), destination_hint(str), message_text(str)."
        )
        user_prompt = f"User query: {query}"
        return self._chat_json(system_prompt, user_prompt)

    def summarize_task(self, query: str, task_status: str, result: dict[str, Any]) -> str:
        system_prompt = (
            "You are an assistant that writes short user-facing summaries in Russian. "
            "Return strict JSON: {\"summary\": \"...\"}. "
            "Keep it concise and factual."
        )
        user_prompt = json.dumps(
            {"query": query, "task_status": task_status, "result": result},
            ensure_ascii=False,
        )
        data = self._chat_json(system_prompt, user_prompt)
        summary = data.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        raise LLMClientError("Missing summary")
