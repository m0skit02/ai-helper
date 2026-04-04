from __future__ import annotations

import json
import os
import socket
import time
from typing import Any
from urllib import error, request


class ToolsClientError(Exception):
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


class ToolsClient:
    def __init__(self) -> None:
        self.url = os.getenv("TOOLS_CALL_URL", "http://127.0.0.1:8080/mcp/tool/call")
        self.timeout_seconds = float(os.getenv("TOOLS_TIMEOUT_SECONDS", "20"))
        self.retries = int(os.getenv("TOOLS_RETRIES", "2"))

    def _classify_http_error(self, exc: error.HTTPError) -> ToolsClientError:
        status = exc.code
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""

        if status in (401, 403):
            category = "auth"
        elif status == 404:
            category = "not_found"
        elif status == 408:
            category = "timeout"
        elif status == 429:
            category = "rate_limit"
        else:
            category = "http_error"

        detail = f"http_{status}"
        if body:
            detail = f"{detail}: {body[:240]}"
        return ToolsClientError(category, detail, status_code=status)

    def _classify_network_error(self, exc: Exception) -> ToolsClientError:
        if isinstance(exc, TimeoutError | socket.timeout):
            return ToolsClientError("timeout", "request timed out")
        if isinstance(exc, error.URLError):
            reason = exc.reason
            if isinstance(reason, TimeoutError | socket.timeout):
                return ToolsClientError("timeout", "request timed out")
            return ToolsClientError("network", str(reason))
        return ToolsClientError("network", str(exc))

    def call_tool(
        self,
        tool: str,
        session_id: str | None,
        input_data: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        payload = {
            "trace_id": trace_id,
            "session_id": session_id,
            "tool": tool,
            "input": input_data,
        }

        last_error: str | None = None
        for attempt in range(self.retries + 1):
            try:
                req = request.Request(
                    self.url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw) if raw else {}

                if not isinstance(data, dict):
                    raise ToolsClientError("invalid_response", "invalid tools response format")

                if data.get("ok") is False:
                    err = data.get("error")
                    if isinstance(err, dict):
                        code = str(err.get("code", "tool_error")).lower()
                        message = str(err.get("message", "tool returned an error"))
                        raise ToolsClientError(code, message)
                    raise ToolsClientError("tool_error", "tool returned ok=false")

                return data
            except error.HTTPError as exc:
                typed_exc = self._classify_http_error(exc)
                last_error = f"{typed_exc.category}: {typed_exc.message}"
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
            except (error.URLError, TimeoutError, socket.timeout) as exc:
                typed_exc = self._classify_network_error(exc)
                last_error = f"{typed_exc.category}: {typed_exc.message}"
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
            except json.JSONDecodeError as exc:
                typed_exc = ToolsClientError("invalid_response", f"invalid json: {exc}")
                last_error = f"{typed_exc.category}: {typed_exc.message}"
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
            except ToolsClientError as exc:
                last_error = f"{exc.category}: {exc.message}"
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue

        raise ToolsClientError("call_failed", f"tool call failed after retries: {last_error or 'unknown error'}")
