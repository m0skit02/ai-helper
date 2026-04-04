from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request


class ToolsClientError(Exception):
    pass


class ToolsClient:
    def __init__(self) -> None:
        self.url = os.getenv("TOOLS_CALL_URL", "http://127.0.0.1:8080/mcp/tool/call")
        self.timeout_seconds = float(os.getenv("TOOLS_TIMEOUT_SECONDS", "20"))
        self.retries = int(os.getenv("TOOLS_RETRIES", "2"))

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
                    raise ToolsClientError("Invalid tools response format")

                return data
            except (error.URLError, TimeoutError, json.JSONDecodeError, ToolsClientError) as exc:
                last_error = str(exc)
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue

        raise ToolsClientError(f"Tool call failed after retries: {last_error}")
