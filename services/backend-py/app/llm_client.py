from __future__ import annotations

import json
import os
import socket
from typing import Any
from urllib import error, request


class LLMClientError(Exception):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.status_code = status_code


class LLMClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.url = os.getenv("OPENAI_URL", "https://api.openai.com/v1/chat/completions")
        self.timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))

    def enabled(self) -> bool:
        return bool(self.api_key)

    def _classify_http_error(self, exc: error.HTTPError) -> LLMClientError:
        status = exc.code
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""

        if status in (401, 403):
            category = "auth"
        elif status == 404:
            category = "model"
        elif status == 429:
            category = "rate_limit"
        elif status == 408:
            category = "timeout"
        elif status == 400 and any(token in body.lower() for token in ("model", "unsupported", "not found")):
            category = "model"
        else:
            category = "http_error"

        detail = f"http_{status}"
        if body:
            detail = f"{detail}: {body[:240]}"
        return LLMClientError(category, detail, status_code=status)

    def _classify_network_error(self, exc: Exception) -> LLMClientError:
        if isinstance(exc, TimeoutError | socket.timeout):
            return LLMClientError("timeout", "request timed out")
        if isinstance(exc, error.URLError):
            reason = exc.reason
            if isinstance(reason, TimeoutError | socket.timeout):
                return LLMClientError("timeout", "request timed out")
            return LLMClientError("network", str(reason))
        return LLMClientError("network", str(exc))

    def _extract_text_content(self, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMClientError("invalid_response", "choices are missing")

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            joined = "\n".join(part for part in parts if part).strip()
            if joined:
                return joined

        raise LLMClientError("invalid_response", "message content is missing")

    def _is_ollama_native(self) -> bool:
        return self.url.rstrip("/").endswith("/api/chat")

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "ollama":
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_payload(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n"
                    "Output only valid JSON. Do not wrap it in markdown. Do not add extra text."
                ),
            },
            {"role": "user", "content": user_prompt},
        ]

        if self._is_ollama_native():
            return {
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": messages,
                "options": {"temperature": 0},
            }

        return {
            "model": self.model,
            "temperature": 0,
            "messages": messages,
        }

    def _extract_response_text(self, data: dict[str, Any]) -> str:
        if self._is_ollama_native():
            message = data.get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            raise LLMClientError("invalid_response", "ollama message content is missing")

        return self._extract_text_content(data)

    def _chat_text(self, system_prompt: str, user_prompt: str) -> str:
        if not self.enabled():
            raise LLMClientError("auth", "OPENAI_API_KEY is not set")

        if self._is_ollama_native():
            payload = {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "options": {"temperature": 0.2},
            }
        else:
            payload = {
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }

        req = request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._build_headers(),
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            raise self._classify_http_error(exc) from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise self._classify_network_error(exc) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMClientError("invalid_response", "openai returned invalid json") from exc

        return self._extract_response_text(data)

    def _parse_json_content(self, raw_text: str) -> dict[str, Any]:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = [line for line in cleaned.splitlines() if not line.startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMClientError("invalid_response", f"model returned non-json: {cleaned[:240]}") from exc

        if not isinstance(parsed, dict):
            raise LLMClientError("invalid_response", "model returned non-object json")
        return parsed

    def _chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.enabled():
            raise LLMClientError("auth", "OPENAI_API_KEY is not set")

        payload = self._build_payload(system_prompt, user_prompt)
        req = request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._build_headers(),
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            raise self._classify_http_error(exc) from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise self._classify_network_error(exc) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMClientError("invalid_response", "openai returned invalid json") from exc

        raw_text = self._extract_response_text(data)
        return self._parse_json_content(raw_text)

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
        raise LLMClientError("invalid_response", "missing summary")

    def answer_query(self, query: str) -> str:
        system_prompt = (
            "Ты полезный русскоязычный ассистент. "
            "Отвечай по существу, без воды. "
            "Если вопрос требует специальных источников, прямо говори об ограничениях. "
            "Не упоминай внутренние статусы, fallback или технические коды."
        )
        answer = self._chat_text(system_prompt, query)
        if answer.strip():
            return answer.strip()
        raise LLMClientError("invalid_response", "empty answer")

    def healthcheck(self) -> dict[str, Any]:
        if not self.enabled():
            return {
                "enabled": False,
                "model": self.model,
                "url": self.url,
                "status": "no_key",
            }

        try:
            data = self._chat_json(
                "Return strict JSON: {\"ok\": true}.",
                "Ping",
            )
            return {
                "enabled": True,
                "model": self.model,
                "url": self.url,
                "status": "ok" if data.get("ok") is True else "invalid_response",
            }
        except LLMClientError as exc:
            return {
                "enabled": True,
                "model": self.model,
                "url": self.url,
                "status": "failed",
                "error_category": exc.category,
                "error": exc.message,
            }
