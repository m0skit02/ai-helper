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

    def plan_intent(self, query: str) -> dict[str, Any]:
        system_prompt = (
            "You are a planner for a universal AI assistant that can answer questions, search the web, "
            "compare products, open pages in a browser, and perform browser actions. "
            "Return strict JSON with fields: "
            "intent(str: open_site|find_product|news_summary|send_message|bulk_message|browser_action_generic|general_answer), "
            "entity(object), filters(object), attributes(object), ranking(object), action(object), "
            "search_query(str), news_topic(str), destination_hint(str), message_text(str), site_url(str), "
            "request_route(str: informational_request|browser_action_request), summary(str). "
            "Rules: "
            "Use open_site when the user mainly wants to open a site or page. "
            "Use find_product for any item search: phones, laptops, cars, apartments, parts, or any purchasable listing. "
            "Use news_summary for recent news or article overviews. "
            "Use send_message for one recipient, bulk_message for multiple recipients. "
            "Use browser_action_generic for login-gated or interactive browser work that is not just messaging. "
            "Use general_answer for plain questions that do not require browser actions. "
            "attributes must be generic and may contain any parsed traits, never rely on a fixed product-specific schema. "
            "If the user asks to open or click the best result, set action.open_best_result=true. "
            "If a browser is required, set request_route=browser_action_request, otherwise informational_request. "
            "If a site is clearly named in Russian, set the real site_url when possible."
        )
        user_prompt = json.dumps({"query": query}, ensure_ascii=False)
        data = self._chat_json(system_prompt, user_prompt)

        allowed_intents = {
            "open_site",
            "find_product",
            "news_summary",
            "send_message",
            "bulk_message",
            "browser_action_generic",
            "general_answer",
        }
        allowed_routes = {"informational_request", "browser_action_request"}

        intent = data.get("intent")
        if not isinstance(intent, str) or intent not in allowed_intents:
            raise LLMClientError("invalid_response", "missing or invalid intent")

        request_route = data.get("request_route")
        if not isinstance(request_route, str) or request_route not in allowed_routes:
            raise LLMClientError("invalid_response", "missing or invalid request_route")

        def as_clean_string(value: Any) -> str:
            return value.strip() if isinstance(value, str) else ""

        def as_object(value: Any) -> dict[str, Any]:
            return value if isinstance(value, dict) else {}

        return {
            "intent": intent,
            "entity": as_object(data.get("entity")),
            "filters": as_object(data.get("filters")),
            "attributes": as_object(data.get("attributes")),
            "ranking": as_object(data.get("ranking")),
            "action": as_object(data.get("action")),
            "search_query": as_clean_string(data.get("search_query")),
            "news_topic": as_clean_string(data.get("news_topic")),
            "destination_hint": as_clean_string(data.get("destination_hint")),
            "message_text": as_clean_string(data.get("message_text")),
            "site_url": as_clean_string(data.get("site_url")),
            "request_route": request_route,
            "summary": as_clean_string(data.get("summary")),
        }

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

    def plan_browser_step(
        self,
        goal: str,
        page: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            "You are a planner for a browser automation agent. "
            "Return strict JSON with fields: "
            "status(str: continue|done|blocked), "
            "message(str), "
            "action(object|null). "
            "If status=continue, action must contain tool='browser.act' and input object with "
            "action(click|type|press), element_id(str), optional text(str), optional key(str). "
            "Only choose element_id values that exist in page.elements. "
            "If page.auth.required is true, return status='blocked'. "
            "Prefer small safe steps: type into search, press Enter, click result, type text, click send. "
            "Do not invent element ids."
        )
        user_prompt = json.dumps(
            {
                "goal": goal,
                "page": page,
                "history": history[-6:],
            },
            ensure_ascii=False,
        )
        data = self._chat_json(system_prompt, user_prompt)
        status = data.get("status")
        if not isinstance(status, str) or status not in {"continue", "done", "blocked"}:
            raise LLMClientError("invalid_response", "missing or invalid planner status")
        message = data.get("message", "")
        if message is not None and not isinstance(message, str):
            raise LLMClientError("invalid_response", "planner message must be a string")
        action = data.get("action")
        if action is not None and not isinstance(action, dict):
            raise LLMClientError("invalid_response", "planner action must be an object")
        return {
            "status": status,
            "message": message.strip() if isinstance(message, str) else "",
            "action": action,
        }

    def plan_navigation_target(
        self,
        query: str,
        site_url_hint: str | None,
    ) -> dict[str, Any]:
        system_prompt = (
            "You plan the first browser navigation step for a browser assistant. "
            "Return strict JSON with fields: "
            "mode(str: open_site|search_then_open|browser_loop), "
            "site_url(str), "
            "search_query(str), "
            "open_url(str), "
            "message(str). "
            "Use open_site only when the user only wants to open a site. "
            "Use search_then_open when the user asks to find a product, page, article, or item on a specific site, "
            "and the assistant should first search/select the best URL before opening it. "
            "Use browser_loop for interactive flows such as login-gated pages, chats, forms, inboxes, and actions inside a site. "
            "Prefer the provided site_url_hint when it matches the user's request. "
            "If the user names a site in Russian, keep the site_url in the proper real domain."
        )
        user_prompt = json.dumps(
            {
                "query": query,
                "site_url_hint": site_url_hint,
            },
            ensure_ascii=False,
        )
        data = self._chat_json(system_prompt, user_prompt)
        mode = data.get("mode")
        if not isinstance(mode, str) or mode not in {"open_site", "search_then_open", "browser_loop"}:
            raise LLMClientError("invalid_response", "missing or invalid navigation mode")

        def as_clean_string(value: Any) -> str:
            return value.strip() if isinstance(value, str) else ""

        return {
            "mode": mode,
            "site_url": as_clean_string(data.get("site_url")),
            "search_query": as_clean_string(data.get("search_query")),
            "open_url": as_clean_string(data.get("open_url")),
            "message": as_clean_string(data.get("message")),
        }

    def choose_best_result(
        self,
        query: str,
        site_url: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            "You rank search results for a browser assistant. "
            "Return strict JSON with fields: selected_index(number), reason(str). "
            "Pick the best result for the user's goal on the requested site. "
            "Prefer direct product or content pages over home pages, ads, or category hubs. "
            "Keep constraints exact: model numbers, memory size, condition, price ceiling, generation, and variant words "
            "such as pro, max, plus, mini, ultra must match the user request instead of drifting to a nearby item. "
            "If the user asks for the cheapest option, prefer the cheapest result when price hints exist."
        )
        user_prompt = json.dumps(
            {
                "query": query,
                "site_url": site_url,
                "results": results[:10],
            },
            ensure_ascii=False,
        )
        data = self._chat_json(system_prompt, user_prompt)
        selected_index = data.get("selected_index")
        if not isinstance(selected_index, int):
            raise LLMClientError("invalid_response", "missing selected_index")
        reason = data.get("reason", "")
        if reason is not None and not isinstance(reason, str):
            raise LLMClientError("invalid_response", "reason must be a string")
        return {
            "selected_index": selected_index,
            "reason": reason.strip() if isinstance(reason, str) else "",
        }

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
