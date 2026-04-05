from __future__ import annotations

import email.utils
import html
import os
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


class NewsClientError(Exception):
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


@dataclass
class NewsQuery:
    topic: str
    limit: int
    days: int | None


class NewsClient:
    def __init__(self) -> None:
        self.timeout_seconds = float(os.getenv("NEWS_TIMEOUT_SECONDS", "15"))
        self.default_limit = int(os.getenv("NEWS_DEFAULT_LIMIT", "5"))
        self.default_days = int(os.getenv("NEWS_DEFAULT_DAYS", "7"))
        self.google_news_base = os.getenv("NEWS_GOOGLE_RSS_URL", "https://news.google.com/rss/search")
        self.language = os.getenv("NEWS_LANGUAGE", "ru")
        self.region = os.getenv("NEWS_REGION", "RU")
        self.enabled_flag = os.getenv("NEWS_RETRIEVER_ENABLED", "true").lower() not in {"0", "false", "no"}

    def enabled(self) -> bool:
        return self.enabled_flag

    def _classify_http_error(self, exc: error.HTTPError) -> NewsClientError:
        status = exc.code
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""

        if status == 429:
            category = "rate_limit"
        elif status == 408:
            category = "timeout"
        else:
            category = "http_error"

        detail = f"http_{status}"
        if body:
            detail = f"{detail}: {body[:240]}"
        return NewsClientError(category, detail, status_code=status)

    def _classify_network_error(self, exc: Exception) -> NewsClientError:
        if isinstance(exc, TimeoutError | socket.timeout):
            return NewsClientError("timeout", "request timed out")
        if isinstance(exc, error.URLError):
            reason = exc.reason
            if isinstance(reason, TimeoutError | socket.timeout):
                return NewsClientError("timeout", "request timed out")
            return NewsClientError("network", str(reason))
        return NewsClientError("network", str(exc))

    def _extract_limit(self, query: str) -> int:
        match = re.search(r"\b(\d{1,2})\s+(?:последн\w*\s+)?новост", query, re.IGNORECASE)
        if match:
            return max(1, min(20, int(match.group(1))))
        return self.default_limit

    def _extract_days(self, query: str) -> int | None:
        match = re.search(r"\bза\s+(\d{1,3})\s+(?:дн(?:я|ей)?|day|days)\b", query, re.IGNORECASE)
        if match:
            return max(1, min(365, int(match.group(1))))
        return self.default_days

    def _extract_topic(self, query: str) -> str:
        topic = query.strip()
        cleanup_patterns = (
            r"(?i)\b\d{1,2}\s+(?:последн\w*\s+)?новост\w*",
            r"(?i)\bпоследн\w*\s+новост\w*",
            r"(?i)\bновост\w*\s+по\b",
            r"(?i)\bновост\w*\s+про\b",
            r"(?i)\bновост\w*\b",
            r"(?i)\bза\s+\d{1,3}\s+(?:дн(?:я|ей)?|day|days)\b",
            r"(?i)\bза\s+сегодня\b",
            r"(?i)\bза\s+неделю\b",
            r"(?i)\bза\s+месяц\b",
        )
        for pattern in cleanup_patterns:
            topic = re.sub(pattern, " ", topic)
        topic = re.sub(r"\s+", " ", topic).strip(" ,.-")
        return topic or query.strip()

    def _build_query(self, query: str, *, limit: int | None = None, days: int | None = None) -> NewsQuery:
        resolved_limit = max(1, min(20, limit if limit is not None else self._extract_limit(query)))
        resolved_days = days if days is not None else self._extract_days(query)
        resolved_topic = self._extract_topic(query)
        return NewsQuery(topic=resolved_topic, limit=resolved_limit, days=resolved_days)

    def _build_url(self, query: NewsQuery) -> str:
        terms = query.topic
        if query.days:
            terms = f"{terms} when:{query.days}d"
        params = {
            "q": terms,
            "hl": self.language,
            "gl": self.region,
            "ceid": f"{self.region}:{self.language}",
        }
        return f"{self.google_news_base}?{parse.urlencode(params)}"

    def _parse_pub_date(self, raw_value: str) -> str | None:
        if not raw_value.strip():
            return None
        try:
            parsed = email.utils.parsedate_to_datetime(raw_value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return raw_value.strip()
        return parsed.isoformat()

    def _strip_html(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value or "")
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    def search_news(self, query: str, *, limit: int | None = None, days: int | None = None) -> list[dict[str, Any]]:
        if not self.enabled():
            raise NewsClientError("disabled", "news retriever is disabled")

        news_query = self._build_query(query, limit=limit, days=days)
        url = self._build_url(news_query)
        req = request.Request(url, headers={"User-Agent": "ai-helper/1.0"})

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            raise self._classify_http_error(exc) from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise self._classify_network_error(exc) from exc

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise NewsClientError("invalid_response", "invalid rss feed") from exc

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source_node = item.find("source")
            source = source_node.text.strip() if source_node is not None and source_node.text else ""

            if not link or link in seen:
                continue
            seen.add(link)

            items.append(
                {
                    "title": title,
                    "summary": self._strip_html(description),
                    "published_at": self._parse_pub_date(pub_date),
                    "url": link,
                    "source": source,
                }
            )
            if len(items) >= news_query.limit:
                break

        return items
