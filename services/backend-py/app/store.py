from __future__ import annotations

import re
import time
import traceback
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse
from uuid import uuid4

from .llm_client import LLMClient, LLMClientError
from .news_client import NewsClient, NewsClientError
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


def requires_browser_action(query: str) -> bool:
    q = query.lower()
    action_patterns = (
        "открой",
        "зайди",
        "перейди",
        "проверь",
        "напиши",
        "отправь",
        "сделай",
        "РЅР°Р№РґРё",
        "open",
        "go to",
        "check",
        "send",
        "write",
        "find",
    )
    site_patterns = (
        "vk",
        "вк",
        "вконтакте",
        "gmail",
        "google mail",
        "mail",
        "telegram",
        "whatsapp",
        "ozon",
        "wb",
        "wildberries",
    )
    return any(pattern in q for pattern in action_patterns) and any(pattern in q for pattern in site_patterns)


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


def is_open_site_request(query: str) -> bool:
    q = query.lower()
    open_patterns = (
        "открой сайт",
        "открой ",
        "зайди на ",
        "перейди на ",
        "open site",
        "open ",
        "go to ",
    )
    return any(pattern in q for pattern in open_patterns)


def infer_generic_site_url(query: str) -> str | None:
    normalized = query.strip()

    explicit_url = re.search(r"(https?://[^\s]+)", normalized, re.IGNORECASE)
    if explicit_url:
        return explicit_url.group(1).rstrip(".,)")

    explicit_domain = re.search(r"\b([a-z0-9-]+\.(?:com|ru|org|net|io|ai|app))\b", normalized, re.IGNORECASE)
    if explicit_domain:
        domain = explicit_domain.group(1).lower()
        return f"https://{domain}"

    lowered = normalized.lower()
    marker_patterns = (
        r"открой сайт\s+([a-zA-Z0-9-]+)",
        r"зайди на сайт\s+([a-zA-Z0-9-]+)",
        r"перейди на сайт\s+([a-zA-Z0-9-]+)",
        r"open site\s+([a-zA-Z0-9-]+)",
    )
    for pattern in marker_patterns:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            site = match.group(1).strip().lower()
            if site:
                return f"https://www.{site}.com/"

    return None


def infer_site_url(query: str) -> str | None:
    q = query.lower()
    if any(token in q for token in ("vk", "вк", "вконтакте")):
        return "https://vk.com/im"
    if "gmail" in q:
        return "https://mail.google.com/"
    if "mail.ru" in q or "mail ru" in q:
        return "https://mail.ru/"
    if "telegram" in q:
        return "https://web.telegram.org/"
    if "whatsapp" in q:
        return "https://web.whatsapp.com/"
    if "ozon" in q:
        return "https://www.ozon.ru/"
    if "wildberries" in q or " wb " in f" {q} ":
        return "https://www.wildberries.ru/"
    return infer_generic_site_url(query)


def build_search_url(query: str, engine: str = "yandex") -> str:
    if engine.lower() == "google":
        return f"https://www.google.com/search?q={quote_plus(query)}"
    return f"https://yandex.ru/search/?text={quote_plus(query)}"


def build_native_site_search_url(site_url: str | None, query: str) -> str | None:
    domain = extract_site_domain(site_url)
    if not domain:
        return None

    compact_query = re.sub(r"(?i)(?:^|\s)-\s*(pro|max|plus|mini|ultra)\b", " ", query)
    compact_query = re.sub(r"(?i)\b(?:без|not)\s+(pro|max|plus|mini|ultra)\b", " ", compact_query)
    compact_query = re.sub(r"\s+", " ", compact_query).strip()
    encoded_query = quote_plus(compact_query)

    patterns = {
        "ozon.ru": f"https://www.ozon.ru/search/?text={encoded_query}",
        "wildberries.ru": f"https://www.wildberries.ru/catalog/0/search.aspx?search={encoded_query}",
        "market.yandex.ru": f"https://market.yandex.ru/search?text={encoded_query}",
        "megamarket.ru": f"https://megamarket.ru/catalog/?q={encoded_query}",
        "sbermegamarket.ru": f"https://megamarket.ru/catalog/?q={encoded_query}",
        "dns-shop.ru": f"https://www.dns-shop.ru/search/?q={encoded_query}",
        "citilink.ru": f"https://www.citilink.ru/search/?text={encoded_query}",
        "mvideo.ru": f"https://www.mvideo.ru/product-list-page?q={encoded_query}",
        "eldorado.ru": f"https://www.eldorado.ru/search/catalog.php?q={encoded_query}",
        "aliexpress.ru": f"https://aliexpress.ru/wholesale?SearchText={encoded_query}",
    }
    if domain in patterns:
        return patterns[domain]
    return None


def requires_message_action_clean(query: str) -> bool:
    q = query.lower()
    patterns = (
        "\u043d\u0430\u043f\u0438\u0448\u0438",
        "\u043e\u0442\u043f\u0440\u0430\u0432\u044c",
        "send",
        "write to",
        "\u043e\u0442\u043f\u0440\u0430\u0432\u044c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435",
        "\u043d\u0430\u043f\u0438\u0448\u0438 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435",
    )
    return any(pattern in q for pattern in patterns)


def wants_product_search_clean(query: str) -> bool:
    q = query.lower()
    keywords = (
        "iphone",
        "samsung",
        "xiaomi",
        "\u0442\u043e\u0432\u0430\u0440",
        "\u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441",
        "\u043a\u0443\u043f\u0438\u0442\u044c",
        "\u0446\u0435\u043d\u0430",
        "\u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c",
        "\u0434\u0435\u0448\u0435\u0432",
        "\u043c\u0438\u043d\u0438\u043c\u0430\u043b\u044c\u043d",
        "price",
        "cheap",
    )
    return any(keyword in q for keyword in keywords)


def is_open_site_request_clean(query: str) -> bool:
    q = query.lower()
    patterns = (
        "\u043e\u0442\u043a\u0440\u043e\u0439 \u0441\u0430\u0439\u0442",
        "\u0437\u0430\u0439\u0434\u0438 \u043d\u0430 \u0441\u0430\u0439\u0442",
        "\u043f\u0435\u0440\u0435\u0439\u0434\u0438 \u043d\u0430 \u0441\u0430\u0439\u0442",
        "open site",
        "go to site",
    )
    if any(pattern in q for pattern in patterns):
        return True

    return bool(re.search(r"\b([a-z0-9-]+\.(?:com|ru|org|net|io|ai|app))\b", q, re.IGNORECASE))


def resolve_site_url(query: str) -> str | None:
    q = query.lower()
    if any(token in q for token in ("vk", "\u0432\u043a", "\u0432\u043a\u043e\u043d\u0442\u0430\u043a\u0442\u0435")):
        return "https://vk.com/im"
    if "gmail" in q:
        return "https://mail.google.com/"
    if "mail.ru" in q or "mail ru" in q:
        return "https://mail.ru/"
    if "telegram" in q:
        return "https://web.telegram.org/"
    if "whatsapp" in q:
        return "https://web.whatsapp.com/"
    if "ozon" in q or "\u043e\u0437\u043e\u043d" in q:
        return "https://www.ozon.ru/"
    if "dns" in q or "\u0434\u043d\u0441" in q:
        return "https://www.dns-shop.ru/"
    if "wildberries" in q or "\u0432\u0430\u0439\u043b\u0434\u0431\u0435\u0440\u0438\u0437" in q or " wb " in f" {q} ":
        return "https://www.wildberries.ru/"
    return infer_generic_site_url(query)


def requires_browser_action_clean(query: str) -> bool:
    q = query.lower()
    action_patterns = (
        "\u043e\u0442\u043a\u0440\u043e\u0439",
        "\u0437\u0430\u0439\u0434\u0438",
        "\u043f\u0435\u0440\u0435\u0439\u0434\u0438",
        "\u043f\u0440\u043e\u0432\u0435\u0440\u044c",
        "\u043d\u0430\u0439\u0434\u0438",
        "\u043d\u0430\u043f\u0438\u0448\u0438",
        "\u043e\u0442\u043f\u0440\u0430\u0432\u044c",
        "\u0441\u0434\u0435\u043b\u0430\u0439",
        "open",
        "go to",
        "check",
        "send",
        "write",
        "find",
    )
    return any(pattern in q for pattern in action_patterns) and resolve_site_url(query) is not None


def extract_site_domain(site_url: str | None) -> str | None:
    if not site_url:
        return None
    parsed = urlparse(site_url)
    hostname = (parsed.hostname or "").lower().strip()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname or None


def is_listing_url(url: str) -> bool:
    lowered = (url or "").lower()
    if any(
        token in lowered
        for token in (
            "/category/",
            "/catalog/",
            "/search/",
            "/brand/",
            "/collections/",
            "/tags/",
            "/filters/",
        )
    ):
        return True
    auto_listing_patterns = (
        r"/cars/[^/]+/[^/]+/\d+/(used|all|new)/do-\d+/?$",
        r"/cars/[^/]+/[^/]+/\d+/(used|all|new)/?$",
    )
    return any(re.search(pattern, lowered) for pattern in auto_listing_patterns)


def is_probable_product_url(url: str) -> bool:
    lowered = (url or "").lower()
    if not lowered or is_listing_url(lowered):
        return False
    product_tokens = ("/product/", "/products/", "/item/", "/goods/", "/dp/", "/p/")
    return any(token in lowered for token in product_tokens) or bool(re.search(r"/\d{5,}", lowered))


def is_search_engine_result_url(url: str) -> bool:
    host = extract_site_domain(url) or ""
    blocked_hosts = {
        "google.com",
        "www.google.com",
        "yandex.ru",
        "www.yandex.ru",
        "ya.ru",
        "bing.com",
        "duckduckgo.com",
    }
    if host in blocked_hosts:
        return True
    lowered = (url or "").lower()
    blocked_tokens = (
        "yandex.ru/an/count",
        "google.com/search",
        "/aclk?",
    )
    return any(token in lowered for token in blocked_tokens)


def query_targets_marketplaces(query: str) -> bool:
    lowered = query.lower()
    markers = (
        "\u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441",
        "\u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441\u043e\u0432",
        "\u0432\u0441\u0435\u0445 \u043c\u0430\u0433\u0430\u0437\u0438\u043d\u043e\u0432",
        "\u0441\u0440\u0435\u0434\u0438 \u043c\u0430\u0433\u0430\u0437\u0438\u043d\u043e\u0432",
        "\u0432\u0441\u0435\u0445 \u043c\u0430\u0440\u043a\u0435\u0442\u043e\u0432",
        "marketplaces",
        "all stores",
        "all marketplaces",
        "ozon",
        "\u043e\u0437\u043e\u043d",
        "wildberries",
        "wb",
        "yandex market",
        "\u044f\u043d\u0434\u0435\u043a\u0441 \u043c\u0430\u0440\u043a\u0435\u0442",
        "market.yandex",
        "dns",
        "\u0434\u043d\u0441",
        "citilink",
        "\u0441\u0438\u0442\u0438\u043b\u0438\u043d\u043a",
        "mvideo",
        "\u043c\u0432\u0438\u0434\u0435\u043e",
        "eldorado",
        "\u044d\u043b\u044c\u0434\u043e\u0440\u0430\u0434\u043e",
        "megamarket",
        "\u043c\u0435\u0433\u0430\u043c\u0430\u0440\u043a\u0435\u0442",
    )
    return any(marker in lowered for marker in markers)


def has_explicit_site_constraint(query: str) -> bool:
    return bool(re.search(r"(?i)\bsite:(?:www\.)?[a-z0-9.-]+\b", query or ""))


def is_allowed_marketplace_domain(url: str) -> bool:
    host = extract_site_domain(url) or ""
    allowed = {
        "ozon.ru",
        "wildberries.ru",
        "market.yandex.ru",
        "megamarket.ru",
        "mvideo.ru",
        "eldorado.ru",
        "dns-shop.ru",
        "citilink.ru",
        "aliexpress.ru",
        "sbermegamarket.ru",
    }
    return host in allowed or any(host.endswith(f".{domain}") for domain in allowed)


def refine_product_search_query(
    query: str,
    site_url: str | None = None,
    *,
    include_negative_variants: bool = True,
) -> str:
    refined = query
    cleanup_patterns = (
        r"(?i)\bнайди\b",
        r"(?i)\bfind\b",
        r"(?i)\bсамый дешевый\b",
        r"(?i)\bсамую дешевую\b",
        r"(?i)\bсамое дешевое\b",
        r"(?i)\bпо самой низкой цене\b",
        r"(?i)\bcheapest\b",
        r"(?i)\blowest price\b",
        r"(?i)\bи открой карточку товара\b",
        r"(?i)\bоткрой карточку товара\b",
        r"(?i)\bopen the product card\b",
        r"(?i)\bopen product card\b",
        r"(?i)\bна ozon\b",
        r"(?i)\bна ozone\b",
        r"(?i)\bon ozon\b",
        r"(?i)\bна wildberries\b",
        r"(?i)\bon wildberries\b",
        r"(?i)\bsite:(?:www\.)?[a-z0-9.-]+\b",
    )
    for pattern in cleanup_patterns:
        refined = re.sub(pattern, " ", refined)

    domain = extract_site_domain(site_url)
    if domain:
        domain_label = domain.split(".")[0]
        refined = re.sub(rf"(?i)\b{re.escape(domain_label)}\b", " ", refined)

    refined = re.sub(r"\s+", " ", refined).strip(" ,.-")

    requested_variants = query_variant_tokens(query, site_url)
    forbidden_variants = forbidden_variant_tokens(query)
    remaining_variants = {"pro", "max", "plus", "mini", "ultra"} - requested_variants - forbidden_variants
    negative_terms = [f"-{variant}" for variant in sorted(remaining_variants)] if include_negative_variants and not requested_variants else []

    final_query = refined or query
    if negative_terms:
        final_query = f"{final_query} {' '.join(negative_terms)}"
    return final_query.strip()


def tokenize_product_query(query: str, site_url: str | None = None) -> list[str]:
    refined = refine_product_search_query(query, site_url, include_negative_variants=False).lower()
    refined = re.sub(r"(?i)(?:^|\s)-\s*(pro|max|plus|mini|ultra)\b", " ", refined)
    tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]+", refined)
    stop_words = {
        "и",
        "на",
        "для",
        "в",
        "с",
        "по",
        "the",
        "and",
        "with",
        "ozon",
        "wildberries",
        "site",
        "www",
        "ru",
        "com",
        "новый",
        "новая",
        "новое",
        "new",
        "найди",
        "find",
        "купить",
        "цена",
        "стоимость",
        "price",
        "дешевый",
        "дешевую",
        "дешевое",
        "cheapest",
        "lowest",
    }
    return [token for token in tokens if len(token) >= 2 and token not in stop_words]


def normalize_product_text(value: str) -> str:
    lowered = (value or "").lower()
    lowered = re.sub(r"(\d+)\s*(gb|гб|tb|тб)\b", r"\1\2", lowered)
    lowered = re.sub(r"[^a-zа-я0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def extract_storage_tokens(query: str, site_url: str | None = None) -> list[str]:
    refined = refine_product_search_query(query, site_url, include_negative_variants=False).lower()
    compact = re.sub(r"\s+", "", refined)
    tokens = re.findall(r"(\d+)(gb|гб|tb|тб)\b", compact)
    return [f"{number}{unit}" for number, unit in tokens]


def extract_model_number_tokens(query: str, site_url: str | None = None) -> list[str]:
    tokens = tokenize_product_query(query, site_url)
    return [token for token in tokens if token.isdigit() and 10 <= int(token) <= 30]


def forbidden_variant_tokens(query: str) -> set[str]:
    lowered = query.lower()
    forbidden = {
        match.group(1)
        for match in re.finditer(r"(?:^|\s)-\s*(pro|max|plus|mini|ultra)\b", lowered)
    }
    forbidden.update(
        match.group(1)
        for match in re.finditer(r"\b(?:без|not)\s+(pro|max|plus|mini|ultra)\b", lowered)
    )
    return forbidden


def query_variant_tokens(query: str, site_url: str | None = None) -> set[str]:
    sanitized = re.sub(r"(?i)(?:^|\s)-\s*(pro|max|plus|mini|ultra)\b", " ", query.lower())
    sanitized = re.sub(r"(?i)\b(?:без|not)\s+(pro|max|plus|mini|ultra)\b", " ", sanitized)
    sanitized = re.sub(r"(?i)\bsite:(?:www\.)?[a-z0-9.-]+\b", " ", sanitized)
    tokens = set(re.findall(r"[a-zA-Zа-яА-Я0-9]+", sanitized))
    variants = {"pro", "max", "plus", "mini", "ultra"}
    forbidden = forbidden_variant_tokens(query)
    return {token for token in tokens if token in variants and token not in forbidden}


def product_condition_matches_query(product: ProductItem, query: str) -> bool:
    if not re.search(r"\b(новый|новая|новое|new)\b", query.lower()):
        return True
    haystack = normalize_product_text(f"{product.title} {product.condition or ''} {product.url}")
    blocked_markers = (
        "used",
        "refurbished",
        "бу",
        "уцен",
        "витрин",
        "восстанов",
    )
    return not any(marker in haystack for marker in blocked_markers)


def parse_price_bounds(query: str) -> tuple[float | None, float | None]:
    lowered = query.lower().replace(" ", "")
    between_match = re.search(r"от(\d+(?:[.,]\d+)?)(млн|тыс|k|m)?до(\d+(?:[.,]\d+)?)(млн|тыс|k|m)?", lowered)
    if between_match:
        lower = normalize_price_value(between_match.group(1), between_match.group(2))
        upper = normalize_price_value(between_match.group(3), between_match.group(4))
        return lower, upper

    max_match = re.search(r"(?:до|небольше|невыше)(\d+(?:[.,]\d+)?)(млн|тыс|k|m)?", lowered)
    min_match = re.search(r"(?:от|неменьше|нениже)(\d+(?:[.,]\d+)?)(млн|тыс|k|m)?", lowered)
    min_price = normalize_price_value(min_match.group(1), min_match.group(2)) if min_match else None
    max_price = normalize_price_value(max_match.group(1), max_match.group(2)) if max_match else None
    return min_price, max_price


def normalize_price_value(number: str | None, suffix: str | None) -> float | None:
    if not number:
        return None
    try:
        value = float(str(number).replace(",", "."))
    except ValueError:
        return None
    suffix = (suffix or "").lower()
    if suffix in {"млн", "m"}:
        value *= 1_000_000
    elif suffix in {"тыс", "k"}:
        value *= 1_000
    return value


def extract_price_from_text(value: str) -> float | None:
    normalized = (value or "").replace("\xa0", " ")
    currency_patterns = (
        r"(\d[\d\s]{2,}(?:[.,]\d+)?)\s*(?:₽|руб|р\b|rub)",
        r"(?:₽|руб|р\b|rub)\s*(\d[\d\s]{2,}(?:[.,]\d+)?)",
    )
    for pattern in currency_patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).replace(" ", "").replace(",", ".")
        try:
            return float(candidate)
        except ValueError:
            continue

    numeric_candidates = re.findall(r"\b\d[\d\s]{3,}\b", normalized)
    for raw in numeric_candidates:
        candidate = raw.replace(" ", "")
        try:
            value_float = float(candidate)
        except ValueError:
            continue
        if value_float >= 1000:
            return value_float
    return None


def parse_price_bounds_v2(query: str) -> tuple[float | None, float | None]:
    lowered = query.lower().replace(" ", "")
    suffix_pattern = r"(\u043c\u043b\u043d|\u043c\u0438\u043b\u043b\u0438\u043e\u043d|\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u0430|\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u043e\u0432|\u0442\u044b\u0441|\u0442\u044b\u0441\u044f\u0447\u0430|\u0442\u044b\u0441\u044f\u0447\u0438|\u0442\u044b\u0441\u044f\u0447|k|m)?"

    between_match = re.search(
        rf"(?:\u043e\u0442)(\d+(?:[.,]\d+)?){suffix_pattern}(?:\u0434\u043e)(\d+(?:[.,]\d+)?){suffix_pattern}",
        lowered,
    )
    if between_match:
        lower = normalize_price_value(between_match.group(1), between_match.group(2))
        upper = normalize_price_value(between_match.group(3), between_match.group(4))
        return lower, upper

    max_match = re.search(
        rf"(?:\u0434\u043e|\u043d\u0435\u0431\u043e\u043b\u044c\u0448\u0435|\u043d\u0435\u0432\u044b\u0448\u0435)(\d+(?:[.,]\d+)?){suffix_pattern}",
        lowered,
    )
    min_match = re.search(
        rf"(?:\u043e\u0442|\u043d\u0435\u043c\u0435\u043d\u044c\u0448\u0435|\u043d\u0435\u043d\u0438\u0436\u0435)(\d+(?:[.,]\d+)?){suffix_pattern}",
        lowered,
    )
    min_price = normalize_price_value(min_match.group(1), min_match.group(2)) if min_match else None
    max_price = normalize_price_value(max_match.group(1), max_match.group(2)) if max_match else None
    return min_price, max_price


def price_within_requested_bounds(product: ProductItem, query: str) -> bool:
    if product.price is None:
        return False
    min_price, max_price = parse_price_bounds_v2(query)
    value = float(product.price)
    if min_price is not None and value < min_price:
        return False
    if max_price is not None and value > max_price:
        return False
    return True


def product_matches_query(product: ProductItem, query: str, site_url: str | None = None) -> bool:
    haystack = normalize_product_text(f"{product.title} {product.url}")
    query_tokens = tokenize_product_query(query, site_url)
    if not query_tokens:
        return True

    if not product_condition_matches_query(product, query):
        return False

    text_tokens = [
        token
        for token in query_tokens
        if token.isalpha() and token not in {"gb", "гб", "tb", "тб", "pro", "max", "plus", "mini", "ultra"}
    ]
    required_text = [token for token in text_tokens if token not in {"apple", "smartfon", "смартфон"}]
    if required_text and not all(token in haystack for token in required_text):
        return False

    storage_tokens = extract_storage_tokens(query, site_url)
    if storage_tokens and not all(token in haystack for token in storage_tokens):
        return False

    model_numbers = extract_model_number_tokens(query, site_url)
    if model_numbers and not all(token in haystack for token in model_numbers):
        return False

    requested_variants = query_variant_tokens(query, site_url)
    forbidden_variants = forbidden_variant_tokens(query)
    haystack_words = set(haystack.split())
    if requested_variants and not requested_variants.issubset(haystack_words):
        return False
    if forbidden_variants and any(token in haystack_words for token in forbidden_variants):
        return False
    if not requested_variants and not forbidden_variants and any(token in haystack_words for token in {"pro", "max", "plus", "mini", "ultra"}):
        return False

    return True


def should_open_product_result(query: str, site_url: str | None = None) -> bool:
    lowered = query.lower()
    if re.search(r"\b(открой|перейди|покажи|open|go to)\b", lowered):
        return True
    if not wants_product_search_clean(query):
        return False
    return bool(site_url) or has_explicit_site_constraint(query) or query_targets_marketplaces(query)


def prefers_lowest_price_product(query: str, site_url: str | None = None) -> bool:
    lowered = query.lower()
    if re.search(r"\b(дешев|cheap|lowest|min)\b", lowered):
        return True
    if not wants_product_search_clean(query):
        return False
    return has_explicit_site_constraint(query) or query_targets_marketplaces(query) or is_allowed_marketplace_domain(site_url or "")


def search_result_matches_product_query(result: dict[str, Any], query: str, site_url: str | None = None) -> bool:
    if not isinstance(result, dict):
        return False
    product = ProductItem(
        title=str(result.get("title", "")),
        url=str(result.get("url", "")),
    )
    return product_matches_query(product, query, site_url)


def score_search_result_match(result: dict[str, Any], query: str, site_url: str | None = None) -> int:
    if not isinstance(result, dict):
        return -1000
    product = ProductItem(
        title=str(result.get("title", "")),
        url=str(result.get("url", "")),
    )
    return score_product_match(product, query, site_url)


def score_product_match(product: ProductItem, query: str, site_url: str | None = None) -> int:
    haystack = normalize_product_text(f"{product.title} {product.url}")
    if not product_matches_query(product, query, site_url):
        return -1000

    score = 0
    for token in tokenize_product_query(query, site_url):
        if token in haystack:
            score += 1
    for token in extract_storage_tokens(query, site_url):
        if token in haystack:
            score += 3
    for token in extract_model_number_tokens(query, site_url):
        if token in haystack:
            score += 5
    return score


def is_offer_list_trigger(text: str) -> bool:
    normalized = normalize_product_text(text)
    if not normalized:
        return False
    trigger_patterns = (
        "другие предложения",
        "предложения от продавцов",
        "другие продавцы",
        "еще предложения",
        "other offers",
        "seller offers",
    )
    return any(pattern in normalized for pattern in trigger_patterns)


def extract_message_destination(query: str) -> str:
    normalized = query.strip()
    if ":" in normalized:
        normalized = normalized.split(":", 1)[0].strip()
    else:
        quote_pairs = [("\"", "\""), ("«", "»")]
        for left, right in quote_pairs:
            if left in normalized and right in normalized:
                start = normalized.find(left)
                end = normalized.rfind(right)
                if 0 <= start < end:
                    normalized = f"{normalized[:start].strip()} {normalized[end + 1 :].strip()}".strip()
                    break

    lower = normalized.lower()

    prefixes = (
        "отправь сообщение",
        "напиши сообщение",
        "отправь",
        "напиши",
        "send message to",
        "write to",
    )
    for prefix in prefixes:
        if lower.startswith(prefix):
            normalized = normalized[len(prefix):].strip(" :,-")
            lower = normalized.lower()
            break

    leading_site_patterns = (
        r"^(?:во|в)\s+вконтакте\b[\s,:-]*",
        r"^(?:во|в)\s+вк\b[\s,:-]*",
        r"^(?:во|в)\s+vk\b[\s,:-]*",
        r"^(?:on\s+)?vk\b[\s,:-]*",
        r"^(?:in|on)\s+telegram\b[\s,:-]*",
        r"^(?:в|во)\s+телеграм(?:е)?\b[\s,:-]*",
        r"^(?:in|on)\s+whatsapp\b[\s,:-]*",
        r"^(?:в|во)\s+whatsapp\b[\s,:-]*",
        r"^(?:in|on)\s+gmail\b[\s,:-]*",
        r"^(?:в|на)\s+gmail\b[\s,:-]*",
    )
    for pattern in leading_site_patterns:
        updated = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip()
        if updated != normalized:
            normalized = updated
            break

    trailing_site_patterns = (
        r"[\s,:-]*(?:во|в)\s+вконтакте\b$",
        r"[\s,:-]*(?:во|в)\s+вк\b$",
        r"[\s,:-]*(?:во|в)\s+vk\b$",
        r"[\s,:-]*on\s+vk\b$",
        r"[\s,:-]*(?:в|во)\s+телеграм(?:е)?\b$",
        r"[\s,:-]*(?:in|on)\s+telegram\b$",
        r"[\s,:-]*(?:в|во)\s+whatsapp\b$",
        r"[\s,:-]*(?:in|on)\s+whatsapp\b$",
        r"[\s,:-]*(?:в|на)\s+gmail\b$",
        r"[\s,:-]*(?:in|on)\s+gmail\b$",
    )
    for pattern in trailing_site_patterns:
        updated = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip()
        if updated != normalized:
            normalized = updated
            break

    return normalized.strip(" \"'")


def extract_message_text(query: str) -> str:
    normalized = query.strip()
    if ":" in normalized:
        return normalized.split(":", 1)[1].strip().strip("\"'")

    quote_pairs = [("\"", "\""), ("«", "»")]
    for left, right in quote_pairs:
        if left in normalized and right in normalized:
            start = normalized.find(left)
            end = normalized.rfind(right)
            if 0 <= start < end:
                value = normalized[start + 1 : end].strip()
                if value:
                    return value

    return normalized


class TaskStore:
    def __init__(
        self,
        tools_client: ToolsClient | None = None,
        llm_client: LLMClient | None = None,
        news_client: NewsClient | None = None,
    ) -> None:
        self._lock = Lock()
        self._tasks: dict[str, TaskResponse] = {}
        self._conversations: dict[str, ConversationResponse] = {}
        self._messages: dict[str, list[MessageItem]] = {}
        self._tools = tools_client
        self._llm = llm_client
        self._news = news_client

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
        news_client: NewsClient | None = None,
    ) -> None:
        with self._lock:
            self._tasks.clear()
            self._conversations.clear()
            self._messages.clear()
            self._tools = tools_client
            self._llm = llm_client
            self._news = news_client

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

    def _route_request(self, query: str, allow_social_actions: bool) -> str:
        site_url = resolve_site_url(query)
        if site_url and is_open_site_request_clean(query):
            return "browser_action_request"
        if allow_social_actions and (
            requires_message_action_clean(query)
            or requires_browser_action_clean(query)
            or (site_url is not None and wants_product_search_clean(query))
        ):
            return "browser_action_request"
        return "informational_request"

    def _call_tool_with_error(
        self,
        trace: list[TraceItem],
        trace_id: str,
        tool: str,
        session_id: str | None,
        input_data: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, ToolsClientError | None]:
        if self._tools is None:
            self._append_trace(trace, f"{tool}_skipped", "no_client", tool)
            return None, None

        try:
            resp = self._tools.call_tool(
                tool=tool,
                session_id=session_id,
                input_data=input_data,
                trace_id=trace_id,
            )
            self._append_trace(trace, f"{tool}_ok", "ok", tool)
            return resp, None
        except ToolsClientError as exc:
            self._append_trace(
                trace,
                f"{tool}_failed",
                "fallback",
                tool,
                detail=f"{exc.category}: {exc.message}",
            )
            return None, exc

    def _call_tool(
        self,
        trace: list[TraceItem],
        trace_id: str,
        tool: str,
        session_id: str | None,
        input_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        resp, _ = self._call_tool_with_error(trace, trace_id, tool, session_id, input_data)
        return resp

    def _humanize_tool_error(self, tool: str, exc: ToolsClientError, query: str) -> str:
        category = exc.category.lower()
        site_url = resolve_site_url(query)
        site_name = site_url.split("//", 1)[-1].split("/", 1)[0] if site_url else "этом сайте"

        if category == "auth_required":
            return f"Я не могу это сделать, пока вы не авторизуетесь на сайте {site_name}."
        if category == "bridge_unavailable":
            return "Расширение браузера сейчас не подключено. Откройте браузер и перезагрузите расширение."
        if category == "captcha_required":
            return f"На сайте {site_name} требуется пройти капчу вручную, после этого я смогу продолжить."
        if category == "navigation_failed":
            return "Я не смог открыть нужную страницу автоматически."
        if category == "element_not_found":
            if tool == "browser.message.draft" and "vk" in site_name:
                return "Я открыл VK, но не смог найти нужный диалог. Проверьте имя получателя и что нужный чат доступен в сообщениях."
            if tool == "browser.message.send":
                return "Сообщение было подготовлено, но я не смог повторно найти поле ввода или кнопку отправки."
            return "Я открыл страницу, но не смог найти нужный элемент интерфейса."
        if category == "timeout":
            return "Сайт не ответил вовремя. Попробуйте повторить запрос чуть позже."
        if category == "rate_limit":
            return "Сайт временно ограничил частоту действий. Попробуйте немного позже."
        return "Я не смог выполнить действие в браузере."

    def _prepare_message_action(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
        plan: dict[str, Any],
    ) -> tuple[str, str | None, str | None]:
        site_url = str(plan.get("site_url") or resolve_site_url(query) or "").strip()
        destination_hint = str(plan.get("destination_hint") or extract_message_destination(query)).strip()
        message_text = str(plan.get("message_text") or extract_message_text(query)).strip()

        if not site_url:
            return "failed", session_id, "Я не смог определить сайт, где нужно подготовить сообщение."
        if not destination_hint:
            return "failed", session_id, "Я не смог определить получателя сообщения."
        if not message_text:
            return "failed", session_id, "Я не смог определить текст сообщения."

        session_id, open_err = self._open_site_for_query(trace, trace_id, session_id, query)
        if open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)

        draft_resp, draft_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.message.draft",
            session_id=session_id,
            input_data={
                "destination_hint": destination_hint,
                "message_text": message_text,
            },
        )
        if draft_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.message.draft", draft_err, site_url or query)
        if not isinstance(draft_resp, dict):
            return "failed", session_id, "Не удалось подготовить сообщение в браузере."

        session_id = str(draft_resp.get("session_id") or session_id or "") or session_id
        draft_output = draft_resp.get("output", {})
        payload = draft_output.copy() if isinstance(draft_output, dict) else {}
        payload.setdefault("site_url", site_url)
        payload.setdefault("destination_hint", destination_hint)
        payload.setdefault("message_text", message_text)
        if session_id is not None:
            payload["session_id"] = session_id

        action_id = str(payload.get("action_id") or uuid4())
        result.actions.append(
            ActionItem(
                action_id=action_id,
                type="message_send",
                status="waiting_confirm",
                payload=payload,
            )
        )
        result.sources = self._merge_sources([site_url], result.sources)
        return "needs_confirmation", session_id, None

    def _run_non_browser_news_retrieval(
        self,
        query: str,
        trace: list[TraceItem],
        result: TaskResult,
    ) -> str | None:
        if self._news is None or not self._news.enabled():
            self._append_trace(trace, "news_layer_skipped", "fallback", "news.layer")
            return None

        try:
            raw_items = self._news.search_news(query)
            self._append_trace(trace, "news_layer_ok", "ok", "news.layer")
        except NewsClientError as exc:
            self._append_trace(
                trace,
                "news_layer_failed",
                "fallback",
                "news.layer",
                detail=f"{exc.category}: {exc.message}",
            )
            return f"Не удалось получить новости без браузера: {exc.message}"

        result.news = self._normalize_news(raw_items)
        result.sources = self._merge_sources([item.url for item in result.news if item.url.strip()], result.sources)
        return None

    def _open_site_for_query(
        self,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        query: str,
    ) -> tuple[str | None, ToolsClientError | None]:
        url = resolve_site_url(query)
        if not url:
            return session_id, None

        resp, err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={"url": url, "activate": True},
        )
        if resp and isinstance(resp, dict):
            return resp.get("session_id") or session_id, None
        return session_id, err

    def _open_search_page(
        self,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        query: str,
        engine: str = "yandex",
        *,
        reuse_active_tab: bool = False,
    ) -> tuple[str | None, ToolsClientError | None]:
        resp, err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={
                "url": build_search_url(query, engine),
                "activate": False,
                "reuse_active_tab": reuse_active_tab,
            },
        )
        if resp and isinstance(resp, dict):
            return resp.get("session_id") or session_id, None
        return session_id, err

    def _run_informational_retrieval(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        plan: dict[str, Any],
        result: TaskResult,
    ) -> tuple[str | None, str | None]:
        if not (plan["wants_product"] or plan["wants_news"]):
            return session_id, None

        self._append_trace(trace, "informational_flow", "ok", "router")
        task_error: str | None = None
        search_resp: dict[str, Any] | None = None
        search_err: ToolsClientError | None = None

        for attempt in range(1, 4):
            search_resp, search_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.search",
                session_id=session_id,
                input_data={"query": plan["search_query"], "engine": "yandex", "limit": 5},
            )
            if search_err is None and isinstance(search_resp, dict):
                session_id = search_resp.get("session_id") or session_id
                if attempt > 1:
                    self._append_trace(
                        trace,
                        "browser.search_retry_recovered",
                        "ok",
                        "browser.search",
                        detail=f"attempt={attempt}",
                    )
                break

            if search_err is None:
                break

            if search_err.category in {"navigation_failed", "element_not_found"} and attempt < 3:
                self._append_trace(
                    trace,
                    "browser.search_retry",
                    "retry",
                    "browser.search",
                    detail=f"attempt={attempt} category={search_err.category}",
                )
                time.sleep(0.8 * attempt)
                continue
            break

        if (search_err is not None or not isinstance(search_resp, dict)) and search_err is not None and search_err.category in {
            "navigation_failed",
            "element_not_found",
        }:
            session_id, open_err = self._open_search_page(
                trace,
                trace_id,
                session_id,
                plan["search_query"],
                "yandex",
                reuse_active_tab=True,
            )
            if open_err is None:
                search_resp, search_err = self._call_tool_with_error(
                    trace=trace,
                    trace_id=trace_id,
                    tool="browser.search",
                    session_id=session_id,
                    input_data={"query": plan["search_query"], "engine": "yandex", "limit": 5},
                )
                if isinstance(search_resp, dict):
                    session_id = search_resp.get("session_id") or session_id
            else:
                search_err = open_err

        if search_err is not None and task_error is None:
            task_error = self._humanize_tool_error("browser.search", search_err, query)

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
                    "limit": 10,
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
        return session_id, task_error

    def _scan_page_with_retry(
        self,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        limit: int = 40,
        attempts: int = 3,
    ) -> tuple[dict[str, Any] | None, ToolsClientError | None]:
        last_resp: dict[str, Any] | None = None
        last_err: ToolsClientError | None = None
        for attempt in range(1, attempts + 1):
            resp, err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.scan",
                session_id=session_id,
                input_data={"limit": limit},
            )
            if err is None and isinstance(resp, dict):
                if attempt > 1:
                    self._append_trace(
                        trace,
                        "browser.scan_retry_recovered",
                        "ok",
                        "browser.scan",
                        detail=f"attempt={attempt}",
                    )
                return resp, None
            last_resp = resp if isinstance(resp, dict) else None
            last_err = err
        return last_resp, last_err

    def _find_and_open_best_result_any_site(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
        search_query: str,
    ) -> tuple[str, str | None, str | None]:
        effective_search_query = search_query.strip() or query
        if wants_product_search_clean(query):
            effective_search_query = refine_product_search_query(search_query or query)

        session_id, open_err = self._open_search_page(trace, trace_id, session_id, effective_search_query, "google")
        if open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)

        search_resp, search_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.search",
            session_id=session_id,
            input_data={"query": effective_search_query, "engine": "google", "limit": 10},
        )
        if search_err is not None or not isinstance(search_resp, dict):
            return (
                "failed",
                session_id,
                self._humanize_tool_error(
                    "browser.search",
                    search_err or ToolsClientError("tool_error", "browser.search failed"),
                    query,
                ),
            )

        session_id = search_resp.get("session_id") or session_id
        raw_results = search_resp.get("output", {}).get("results", [])
        if not isinstance(raw_results, list) or not raw_results:
            return "failed", session_id, "Я не нашёл подходящих результатов по этому запросу."

        filtered_results: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url or is_search_engine_result_url(url):
                continue
            if wants_product_search_clean(query) and query_targets_marketplaces(query) and not is_allowed_marketplace_domain(url):
                continue
            filtered_results.append(item)

        if not filtered_results:
            return "failed", session_id, "Я не нашёл пригодных ссылок для открытия."

        if wants_product_search_clean(query):
            product_like = [item for item in filtered_results if is_probable_product_url(str(item.get("url", "")).strip())]
            if product_like:
                matched_product_like = [item for item in product_like if search_result_matches_product_query(item, query)]
                filtered_results = matched_product_like or product_like
                self._append_trace(trace, "product_url_candidates_filtered", "ok", "ranker", detail="global")
            else:
                matched_results = [item for item in filtered_results if search_result_matches_product_query(item, query)]
                if matched_results:
                    filtered_results = matched_results
                    self._append_trace(trace, "product_results_query_filtered", "ok", "ranker", detail="global")

            scored_results = [
                (score_search_result_match(item, query), item)
                for item in filtered_results
            ]
            positive_results = [item for score, item in scored_results if score > 0]
            if positive_results:
                filtered_results = positive_results
            else:
                self._append_trace(trace, "product_match_ranked", "fallback", "ranker", detail="no_positive_score_global")

            session_id, cheapest_product = self._extract_best_product_candidate(
                query=query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                candidates=filtered_results,
                result=result,
            )
            if cheapest_product is not None:
                return "done", session_id, "Открыл самый дешёвый найденный товар по запросу."
            return "failed", session_id, "Я нашёл результаты, но не смог подтвердить подходящую карточку товара."

        if wants_product_search_clean(query):
            return "failed", session_id, "Я нашёл результаты, но не смог подтвердить подходящую карточку товара."

        selected = filtered_results[0]
        if self._llm is not None and self._llm.enabled():
            try:
                decision = self._llm.choose_best_result(query, "web", filtered_results)
                self._append_trace(trace, "llm_choose_result_ok", "ok", "llm.choose_result")
                index = int(decision.get("selected_index", 0))
                if 0 <= index < len(filtered_results):
                    selected = filtered_results[index]
            except (LLMClientError, ValueError, TypeError) as exc:
                self._append_trace(
                    trace,
                    "llm_choose_result_failed",
                    "fallback",
                    "llm.choose_result",
                    detail=str(exc),
                )

        selected_url = str(selected.get("url", "")).strip()
        open_resp, result_open_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={"url": selected_url, "activate": True},
        )
        if result_open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", result_open_err, query)

        session_id = open_resp.get("session_id") if isinstance(open_resp, dict) else session_id
        result.sources = self._merge_sources([selected_url], result.sources)

        if wants_product_search_clean(query) and is_listing_url(selected_url):
            session_id, cheapest_from_listing = self._open_listing_and_pick_best_product(
                query=query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                result=result,
                listing_url=selected_url,
            )
            if cheapest_from_listing is not None:
                return "done", session_id, "Открыл самый дешёвый найденный товар по запросу."
            return "failed", session_id, "Я открыл листинг, но не смог выбрать конкретную подходящую карточку товара."

        return "done", session_id, "Открыл наиболее подходящую страницу по вашему запросу."

    def _plan_navigation_target(
        self,
        query: str,
        trace: list[TraceItem],
    ) -> dict[str, str]:
        site_url_hint = resolve_site_url(query) or ""
        fallback_mode = "browser_loop" if requires_message_action_clean(query) else "search_then_open"
        if is_open_site_request_clean(query) and not wants_product_search_clean(query):
            fallback_mode = "open_site"
        fallback = {
            "mode": fallback_mode,
            "site_url": site_url_hint,
            "search_query": query,
            "open_url": "",
            "message": "",
        }

        if self._llm is None or not self._llm.enabled():
            self._append_trace(trace, "llm_navigation_plan_skipped", "fallback", "llm.navigation")
            return fallback

        try:
            plan = self._llm.plan_navigation_target(query, site_url_hint or None)
            self._append_trace(trace, "llm_navigation_plan_ok", "ok", "llm.navigation")
        except LLMClientError as exc:
            self._append_trace(
                trace,
                "llm_navigation_plan_failed",
                "fallback",
                "llm.navigation",
                detail=f"{exc.category}: {exc.message}",
            )
            return fallback

        normalized = {
            "mode": str(plan.get("mode") or fallback["mode"]),
            "site_url": str(plan.get("site_url") or site_url_hint or "").strip(),
            "search_query": str(plan.get("search_query") or query).strip(),
            "open_url": str(plan.get("open_url") or "").strip(),
            "message": str(plan.get("message") or "").strip(),
        }
        if normalized["mode"] not in {"open_site", "search_then_open", "browser_loop"}:
            normalized["mode"] = fallback["mode"]
        if not normalized["site_url"]:
            normalized["site_url"] = site_url_hint
        if not normalized["search_query"]:
            normalized["search_query"] = query
        return normalized

    def _find_and_open_best_site_result_v2(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
        site_url: str,
        search_query: str,
    ) -> tuple[str, str | None, str | None]:
        domain = extract_site_domain(site_url)
        if not domain:
            return "failed", session_id, "Я не смог определить сайт для поиска результата."

        if wants_product_search_clean(query) and is_allowed_marketplace_domain(site_url):
            listing_query = refine_product_search_query(search_query, site_url, include_negative_variants=False)
            native_search_url = build_native_site_search_url(site_url, listing_query)
            if native_search_url:
                self._append_trace(trace, "marketplace_native_search_selected", "ok", "router", detail=domain)
                session_id, cheapest_from_listing = self._open_listing_and_pick_best_product(
                    query=query,
                    trace=trace,
                    trace_id=trace_id,
                    session_id=session_id,
                    result=result,
                    listing_url=native_search_url,
                )
                if cheapest_from_listing is not None:
                    return "done", session_id, f"Открыл самый дешёвый найденный товар на сайте {domain}."
                self._append_trace(
                    trace,
                    "marketplace_native_search_failed",
                    "failed",
                    "browser.open",
                    detail=domain,
                )
                return "failed", session_id, f"Я открыл листинг на сайте {domain}, но не смог выбрать подходящую карточку товара."

        effective_search_query = search_query.strip()
        if wants_product_search_clean(query):
            effective_search_query = refine_product_search_query(search_query, site_url)
        constrained_query = f"site:{domain} {effective_search_query}"
        session_id, open_err = self._open_search_page(trace, trace_id, session_id, constrained_query, "google")
        if open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)

        search_resp, search_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.search",
            session_id=session_id,
            input_data={"query": constrained_query, "engine": "google", "limit": 8},
        )
        if search_err is not None or not isinstance(search_resp, dict):
            return (
                "failed",
                session_id,
                self._humanize_tool_error(
                    "browser.search",
                    search_err or ToolsClientError("tool_error", "browser.search failed"),
                    query,
                ),
            )

        session_id = search_resp.get("session_id") or session_id
        raw_results = search_resp.get("output", {}).get("results", [])
        if not isinstance(raw_results, list) or not raw_results:
            return "failed", session_id, f"Я не нашёл релевантных результатов на сайте {domain}."

        filtered_results: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            host = extract_site_domain(url)
            if not url or not host or (host != domain and not host.endswith(f".{domain}")):
                continue
            filtered_results.append(item)

        if not filtered_results:
            return "failed", session_id, f"Я не нашёл подходящих страниц на сайте {domain}."

        if wants_product_search_clean(query):
            product_like = [item for item in filtered_results if is_probable_product_url(str(item.get("url", "")).strip())]
            if product_like:
                matched_product_like = [item for item in product_like if search_result_matches_product_query(item, query, site_url)]
                filtered_results = matched_product_like or product_like
                self._append_trace(trace, "product_url_candidates_filtered", "ok", "ranker")
            else:
                matched_results = [item for item in filtered_results if search_result_matches_product_query(item, query, site_url)]
                if matched_results:
                    filtered_results = matched_results
                    self._append_trace(trace, "product_results_query_filtered", "ok", "ranker")
                non_listing = [item for item in filtered_results if not is_listing_url(str(item.get("url", "")).strip())]
                if non_listing:
                    filtered_results = non_listing
                    self._append_trace(trace, "listing_urls_filtered", "ok", "ranker")

            scored_results = [
                (score_search_result_match(item, query, site_url), item)
                for item in filtered_results
            ]
            positive_results = [item for score, item in scored_results if score > 0]
            if positive_results:
                filtered_results = positive_results

        if wants_product_search_clean(query):
            session_id, cheapest_product = self._extract_best_product_candidate(
                query=query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                candidates=filtered_results,
                result=result,
            )
            if cheapest_product is not None:
                return "done", session_id, f"Открыл самый дешёвый найденный товар на сайте {domain}."

        selected = filtered_results[0]
        if self._llm is not None and self._llm.enabled():
            try:
                decision = self._llm.choose_best_result(query, site_url, filtered_results)
                self._append_trace(trace, "llm_choose_result_ok", "ok", "llm.choose_result")
                index = int(decision.get("selected_index", 0))
                if 0 <= index < len(filtered_results):
                    selected = filtered_results[index]
            except (LLMClientError, ValueError, TypeError) as exc:
                self._append_trace(
                    trace,
                    "llm_choose_result_failed",
                    "fallback",
                    "llm.choose_result",
                    detail=str(exc),
                )

        selected_url = str(selected.get("url", "")).strip()
        open_resp, result_open_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={"url": selected_url, "activate": True},
        )
        if result_open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", result_open_err, query)

        session_id = open_resp.get("session_id") if isinstance(open_resp, dict) else session_id
        result.sources = self._merge_sources([selected_url])
        return "done", session_id, f"Открыл наиболее подходящую страницу на сайте {domain}."

    def _extract_best_product_candidate(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        candidates: list[dict[str, Any]],
        result: TaskResult,
    ) -> tuple[str | None, ProductItem | None]:
        evaluated: list[ProductItem] = []

        for candidate in candidates[:5]:
            if not isinstance(candidate, dict):
                continue
            candidate_url = str(candidate.get("url", "")).strip()
            candidate_title = str(candidate.get("title", "")).strip()
            if not candidate_url:
                continue

            if is_listing_url(candidate_url):
                session_id, listing_product = self._open_listing_and_pick_best_product(
                    query=query,
                    trace=trace,
                    trace_id=trace_id,
                    session_id=session_id,
                    result=result,
                    listing_url=candidate_url,
                )
                if listing_product is not None:
                    evaluated.append(listing_product)
                continue

            open_resp, open_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.open",
                session_id=session_id,
                input_data={"url": candidate_url, "activate": True},
            )
            if open_err is not None:
                continue
            if isinstance(open_resp, dict):
                session_id = open_resp.get("session_id") or session_id

            offers_opened = False
            scan_resp, scan_err = self._scan_page_with_retry(
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                limit=80,
            )
            if scan_err is None and isinstance(scan_resp, dict):
                elements = scan_resp.get("output", {}).get("elements", [])
                if isinstance(elements, list):
                    for element in elements:
                        if not isinstance(element, dict):
                            continue
                        if not element.get("clickable"):
                            continue
                        text = str(element.get("text", "") or element.get("aria_label", "") or "")
                        element_id = str(element.get("element_id", "")).strip()
                        if not element_id or not is_offer_list_trigger(text):
                            continue
                        act_resp, act_err = self._call_tool_with_error(
                            trace=trace,
                            trace_id=trace_id,
                            tool="browser.act",
                            session_id=session_id,
                            input_data={"action": "click", "element_id": element_id},
                        )
                        if act_err is None and isinstance(act_resp, dict):
                            offers_opened = True
                            self._append_trace(trace, "seller_offers_opened", "ok", "browser.act", detail=text[:120])
                            break

            extract_resp, extract_err = self._call_tool_with_error(
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
                    "limit": 3,
                },
            )
            if extract_err is not None or not isinstance(extract_resp, dict):
                continue

            items = extract_resp.get("output", {}).get("items", [])
            if not isinstance(items, list):
                continue

            fallback_product: ProductItem | None = None
            for item in items:
                if not isinstance(item, dict):
                    continue
                product = self._normalize_product(item)
                if not product.url.strip():
                    product.url = candidate_url
                if not product.title.strip():
                    product.title = candidate_title
                if not self._has_product_data(product) or product.price is None:
                    continue
                if fallback_product is None:
                    fallback_product = product
                if not price_within_requested_bounds(product, query):
                    continue
                if offers_opened:
                    product.title = product.title or candidate_title or "Offer"
                    product.url = candidate_url
                else:
                    candidate_product = ProductItem(title=candidate_title, url=candidate_url)
                    candidate_score = score_product_match(candidate_product, query)
                    extracted_score = score_product_match(product, query)
                    if not product_matches_query(product, query) and candidate_score <= 0:
                        continue
                    if extracted_score <= 0 and candidate_score <= 0:
                        continue
                    if extracted_score <= 0 and candidate_score > 0:
                        product.title = product.title or candidate_title
                        product.url = candidate_url
                evaluated.append(product)
                break
            if fallback_product is not None and not evaluated:
                fallback_product.title = fallback_product.title or candidate_title or "Product"
                fallback_product.url = fallback_product.url or candidate_url
                evaluated.append(fallback_product)
                self._append_trace(trace, "product_extract_fallback_used", "fallback", "browser.extract", detail=candidate_url[:160])

        if not evaluated:
            return session_id, None

        cheapest = min(evaluated, key=lambda item: float(item.price or 0))
        result.product = cheapest
        result.sources = self._merge_sources([cheapest.url], result.sources)
        return session_id, cheapest

    def _extract_products_from_current_page(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        current_url: str,
    ) -> list[ProductItem]:
        priced_items: list[ProductItem] = []
        for _attempt in range(3):
            extract_resp, extract_err = self._call_tool_with_error(
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
                    "limit": 50,
                },
            )
            if extract_err is not None or not isinstance(extract_resp, dict):
                continue

            items = extract_resp.get("output", {}).get("items", [])
            if not isinstance(items, list):
                continue

            normalized_items: list[ProductItem] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                product = self._normalize_product(item)
                if not self._has_product_data(product) or not product.url.strip():
                    continue
                lowered_url = product.url.strip().lower()
                if lowered_url == current_url.strip().lower():
                    continue
                if is_listing_url(lowered_url):
                    continue
                normalized_items.append(product)

            priced_items = [
                item
                for item in normalized_items
                if item.price is not None
                and float(item.price or 0) > 0
                and product_matches_query(item, query)
                and score_product_match(item, query) > 0
                and price_within_requested_bounds(item, query)
            ]
            if priced_items:
                break
        return priced_items

    def _open_best_listing_candidate_from_scan(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
        listing_url: str,
    ) -> tuple[str | None, ProductItem | None]:
        scan_resp, scan_err = self._scan_page_with_retry(
            trace=trace,
            trace_id=trace_id,
            session_id=session_id,
            limit=100,
        )
        if scan_err is not None or not isinstance(scan_resp, dict):
            return session_id, None

        page = scan_resp.get("output", {})
        elements = page.get("elements", []) if isinstance(page, dict) else []
        if not isinstance(elements, list):
            return session_id, None

        evaluated: list[tuple[int, float, dict[str, Any], ProductItem]] = []
        for element in elements:
            if not isinstance(element, dict) or not element.get("clickable"):
                continue
            element_id = str(element.get("element_id", "")).strip()
            element_text = str(element.get("text", "") or element.get("aria_label", "") or "").strip()
            href = str(element.get("href", "")).strip()
            if not element_id or not element_text:
                continue
            if is_offer_list_trigger(element_text):
                continue

            absolute_href = urljoin(listing_url, href) if href else ""
            candidate = ProductItem(
                title=element_text,
                url=absolute_href,
                price=extract_price_from_text(element_text),
            )
            score = score_product_match(candidate, query)
            if score <= 0:
                continue
            if absolute_href and is_listing_url(absolute_href):
                continue
            price_rank = float(candidate.price) if candidate.price is not None else 10**12
            evaluated.append((score, price_rank, element, candidate))

        if not evaluated:
            return session_id, None

        max_score = max(score for score, _price, _element, _candidate in evaluated)
        strongest = [item for item in evaluated if item[0] == max_score]
        _score, _price_rank, chosen_element, chosen_candidate = min(strongest, key=lambda item: item[1])

        chosen_url = chosen_candidate.url.strip()
        if chosen_url and is_probable_product_url(chosen_url):
            open_resp, open_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.open",
                session_id=session_id,
                input_data={"url": chosen_url, "activate": True},
            )
            if open_err is None and isinstance(open_resp, dict):
                session_id = open_resp.get("session_id") or session_id
                self._append_trace(trace, "listing_scan_candidate_opened", "ok", "browser.open", detail=chosen_url[:160])
        else:
            act_resp, act_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.act",
                session_id=session_id,
                input_data={"action": "click", "element_id": str(chosen_element.get("element_id", "")).strip()},
            )
            if act_err is not None or not isinstance(act_resp, dict):
                return session_id, None
            session_id = act_resp.get("session_id") or session_id
            self._append_trace(trace, "listing_scan_candidate_clicked", "ok", "browser.act", detail=chosen_candidate.title[:160])

        extracted = self._extract_products_from_current_page(
            query=query,
            trace=trace,
            trace_id=trace_id,
            session_id=session_id,
            current_url=listing_url,
        )
        if extracted:
            max_score = max(score_product_match(item, query) for item in extracted)
            strongest_products = [item for item in extracted if score_product_match(item, query) == max_score]
            chosen_product = min(strongest_products, key=lambda item: float(item.price or 0))
        else:
            chosen_product = chosen_candidate

        result.product = chosen_product
        result.sources = self._merge_sources([chosen_product.url], result.sources)
        return session_id, chosen_product

    def _open_listing_and_pick_best_product(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
        listing_url: str,
    ) -> tuple[str | None, ProductItem | None]:
        open_resp, open_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={"url": listing_url, "activate": True},
        )
        if open_err is not None:
            return session_id, None
        if isinstance(open_resp, dict):
            session_id = open_resp.get("session_id") or session_id

        priced_items = self._extract_products_from_current_page(
            query=query,
            trace=trace,
            trace_id=trace_id,
            session_id=session_id,
            current_url=listing_url,
        )
        if not priced_items:
            session_id, scan_candidate = self._open_best_listing_candidate_from_scan(
                query=query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                result=result,
                listing_url=listing_url,
            )
            if scan_candidate is None:
                return session_id, None
            return session_id, scan_candidate

        max_score = max(score_product_match(item, query) for item in priced_items)
        strongest = [item for item in priced_items if score_product_match(item, query) == max_score]
        cheapest = min(strongest, key=lambda item: float(item.price or 0))
        result.product = cheapest
        result.sources = self._merge_sources([cheapest.url], result.sources)

        cheapest_open_resp, cheapest_open_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={"url": cheapest.url.strip(), "activate": True},
        )
        if cheapest_open_err is None and isinstance(cheapest_open_resp, dict):
            session_id = cheapest_open_resp.get("session_id") or session_id
            self._append_trace(trace, "listing_product_opened", "ok", "browser.open", detail=cheapest.url[:160])
        return session_id, cheapest

    def _run_browser_action_agent_v2(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
    ) -> tuple[str, str | None, str | None]:
        site_url_hint = (resolve_site_url(query) or infer_site_url(query) or "").strip()
        if wants_product_search_clean(query) and site_url_hint and is_allowed_marketplace_domain(site_url_hint):
            self._append_trace(
                trace,
                "marketplace_product_short_circuit",
                "ok",
                "router",
                detail=extract_site_domain(site_url_hint) or site_url_hint,
            )
            return self._find_and_open_best_site_result_v3(
                query=query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                result=result,
                site_url=site_url_hint,
                search_query=query,
            )

        if self._llm is None or not self._llm.enabled():
            return (
                "failed",
                session_id,
                "Для пошагового браузерного сценария сейчас недоступна LLM-модель.",
            )

        navigation_plan = self._plan_navigation_target(query, trace)
        site_url = (navigation_plan.get("site_url") or resolve_site_url(query) or "").strip()
        mode = (navigation_plan.get("mode") or "browser_loop").strip()
        open_url = (navigation_plan.get("open_url") or "").strip()
        search_query = (navigation_plan.get("search_query") or query).strip()

        if open_url:
            open_resp, open_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.open",
                session_id=session_id,
                input_data={"url": open_url, "activate": True},
            )
            if open_err is not None:
                return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)
            if isinstance(open_resp, dict):
                session_id = open_resp.get("session_id") or session_id
            result.sources = self._merge_sources([open_url])
            return "done", session_id, navigation_plan.get("message") or f"Открыл страницу: {open_url}"

        if mode == "search_then_open" and not site_url:
            return self._find_and_open_best_result_any_site(
                query=query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                result=result,
                search_query=search_query,
            )

        if not site_url:
            return (
                "failed",
                session_id,
                "Я пока не смог понять, какой сайт или страницу нужно открыть для этого действия.",
            )

        if mode == "open_site":
            session_id, open_err = self._open_site_for_query(trace, trace_id, session_id, query)
            if open_err is not None:
                return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)
            return "done", session_id, f"Открыл сайт: {site_url}"

        if mode == "search_then_open":
            return self._find_and_open_best_site_result_v3(
                query,
                trace,
                trace_id,
                session_id,
                result,
                site_url,
                search_query,
            )

        open_resp, open_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={"url": site_url, "activate": True},
        )
        if open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)
        if isinstance(open_resp, dict):
            session_id = open_resp.get("session_id") or session_id

        history: list[dict[str, Any]] = []
        for step_index in range(1, 9):
            scan_resp, scan_err = self._scan_page_with_retry(
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                limit=40,
            )
            if scan_err is not None or not isinstance(scan_resp, dict):
                return (
                    "failed",
                    session_id,
                    self._humanize_tool_error(
                        "browser.scan",
                        scan_err or ToolsClientError("tool_error", "browser.scan failed"),
                        query,
                    ),
                )

            page = scan_resp.get("output", {})
            auth_state = page.get("auth", {}) if isinstance(page, dict) else {}
            if isinstance(auth_state, dict) and auth_state.get("required"):
                message = auth_state.get("message")
                return "failed", session_id, str(message or f"Я не могу это сделать, пока вы не авторизуетесь на сайте {site_url}.")

            try:
                plan = self._llm.plan_browser_step(query, page, history)
                self._append_trace(trace, f"browser_agent_step_{step_index}", "ok", "llm.browser.plan")
            except LLMClientError as exc:
                self._append_trace(
                    trace,
                    f"browser_agent_step_{step_index}",
                    "failed",
                    "llm.browser.plan",
                    detail=f"{exc.category}: {exc.message}",
                )
                return "failed", session_id, "Не удалось построить следующий шаг браузерного сценария."

            status = str(plan.get("status", "blocked"))
            message = str(plan.get("message", "")).strip() or None
            action = plan.get("action")

            history.append(
                {
                    "step": step_index,
                    "planner_status": status,
                    "planner_message": message,
                    "planner_action": action,
                    "page_url": page.get("url") if isinstance(page, dict) else None,
                }
            )

            if status == "done":
                return "done", session_id, message

            if status == "blocked":
                return "failed", session_id, message or "Браузерный сценарий остановлен планировщиком."

            if not isinstance(action, dict):
                return "failed", session_id, "LLM не вернула корректное действие для браузера."

            tool = str(action.get("tool", ""))
            input_data = action.get("input")
            if tool != "browser.act" or not isinstance(input_data, dict):
                return "failed", session_id, "LLM вернула неподдерживаемое браузерное действие."

            act_resp, act_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.act",
                session_id=session_id,
                input_data=input_data,
            )
            if act_err is not None or not isinstance(act_resp, dict):
                return (
                    "failed",
                    session_id,
                    self._humanize_tool_error(
                        "browser.act",
                        act_err or ToolsClientError("tool_error", "browser.act failed"),
                        query,
                    ),
                )

            history.append(
                {
                    "step": step_index,
                    "action_result": act_resp.get("output", {}),
                }
            )

        return "failed", session_id, "Не удалось завершить браузерный сценарий за допустимое число шагов."

    def _find_and_open_best_site_result_v3(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
        site_url: str,
        search_query: str,
    ) -> tuple[str, str | None, str | None]:
        status, session_id, message = self._find_and_open_best_site_result_v2(
            query=query,
            trace=trace,
            trace_id=trace_id,
            session_id=session_id,
            result=result,
            site_url=site_url,
            search_query=search_query,
        )
        if status != "done" or not wants_product_search_clean(query):
            return status, session_id, message

        if result.product is None and result.sources:
            primary_source = result.sources[0]
            if primary_source and is_listing_url(primary_source):
                session_id, cheapest_from_listing = self._open_listing_and_pick_best_product(
                    query=query,
                    trace=trace,
                    trace_id=trace_id,
                    session_id=session_id,
                    result=result,
                    listing_url=primary_source,
                )
                if cheapest_from_listing is not None:
                    return "done", session_id, f"Открыл самый дешёвый найденный товар на сайте {extract_site_domain(site_url) or site_url}."

        priced_items: list[ProductItem] = []
        current_url = result.sources[0] if result.sources else site_url
        priced_items = self._extract_products_from_current_page(
            query=query,
            trace=trace,
            trace_id=trace_id,
            session_id=session_id,
            current_url=current_url,
        )

        if not priced_items:
            return status, session_id, message

        scored_items = [
            (score_product_match(item, query, site_url), item)
            for item in priced_items
        ]
        max_score = max(score for score, _item in scored_items)
        if max_score > 0:
            priced_items = [item for score, item in scored_items if score == max_score]
            self._append_trace(trace, "product_match_ranked", "ok", "ranker", detail=f"matched_tokens={max_score}")
        else:
            self._append_trace(trace, "product_match_ranked", "fallback", "ranker", detail="no_token_match")

        cheapest = min(priced_items, key=lambda item: float(item.price or 0))
        result.product = cheapest
        result.sources = self._merge_sources([cheapest.url], result.sources)

        if cheapest.url.strip():
            cheapest_open_resp, cheapest_open_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.open",
                session_id=session_id,
                input_data={"url": cheapest.url.strip(), "activate": True},
            )
            if cheapest_open_err is None and isinstance(cheapest_open_resp, dict):
                session_id = cheapest_open_resp.get("session_id") or session_id

        return "done", session_id, f"Открыл самый дешёвый найденный товар на сайте {extract_site_domain(site_url) or site_url}."

    def _find_and_open_best_site_result(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
        site_url: str | None = None,
        search_query: str | None = None,
    ) -> tuple[str, str | None, str | None]:
        site_url = site_url or resolve_site_url(query)
        domain = extract_site_domain(site_url)
        if not site_url or not domain:
            return "failed", session_id, "Я не смог определить сайт для поиска результата."

        constrained_query = f"site:{domain} {(search_query or query).strip()}"
        session_id, open_err = self._open_search_page(trace, trace_id, session_id, constrained_query, "google")
        if open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)

        search_resp, search_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.search",
            session_id=session_id,
            input_data={"query": constrained_query, "engine": "google", "limit": 8},
        )
        if search_err is not None or not isinstance(search_resp, dict):
            return (
                "failed",
                session_id,
                self._humanize_tool_error(
                    "browser.search",
                    search_err or ToolsClientError("tool_error", "browser.search failed"),
                    query,
                ),
            )

        session_id = search_resp.get("session_id") or session_id
        raw_results = search_resp.get("output", {}).get("results", [])
        if not isinstance(raw_results, list) or not raw_results:
            return "failed", session_id, f"Я не нашёл релевантных результатов на сайте {domain}."

        filtered_results: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            host = extract_site_domain(url)
            if not url or not host or (host != domain and not host.endswith(f".{domain}")):
                continue
            filtered_results.append(item)

        if not filtered_results:
            return "failed", session_id, f"Я не нашёл подходящих страниц на сайте {domain}."

        selected = filtered_results[0]
        if self._llm is not None and self._llm.enabled():
            try:
                decision = self._llm.choose_best_result(query, site_url, filtered_results)
                self._append_trace(trace, "llm_choose_result_ok", "ok", "llm.choose_result")
                index = int(decision.get("selected_index", 0))
                if 0 <= index < len(filtered_results):
                    selected = filtered_results[index]
            except (LLMClientError, ValueError, TypeError) as exc:
                self._append_trace(
                    trace,
                    "llm_choose_result_failed",
                    "fallback",
                    "llm.choose_result",
                    detail=str(exc),
                )

        open_resp, result_open_err = self._call_tool_with_error(
            trace=trace,
            trace_id=trace_id,
            tool="browser.open",
            session_id=session_id,
            input_data={"url": str(selected.get("url", "")).strip(), "activate": True},
        )
        if result_open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", result_open_err, query)

        session_id = open_resp.get("session_id") if isinstance(open_resp, dict) else session_id
        result.sources = self._merge_sources([str(selected.get("url", "")).strip()])
        return "done", session_id, f"Открыл наиболее подходящую страницу на сайте {domain}."

    def _run_browser_action_agent(
        self,
        query: str,
        trace: list[TraceItem],
        trace_id: str,
        session_id: str | None,
        result: TaskResult,
    ) -> tuple[str, str | None, str | None]:
        if self._llm is None or not self._llm.enabled():
            return (
                "failed",
                session_id,
                "Для пошагового браузерного сценария сейчас недоступна LLM-модель.",
            )

        site_url = resolve_site_url(query)
        if not site_url:
            return (
                "failed",
                session_id,
                "Я пока не смог понять, какой сайт нужно открыть для этого действия.",
            )

        session_id, open_err = self._open_site_for_query(trace, trace_id, session_id, query)
        if open_err is not None:
            return "failed", session_id, self._humanize_tool_error("browser.open", open_err, query)

        if is_open_site_request_clean(query) and not wants_product_search_clean(query):
            return "done", session_id, f"Открыл сайт: {site_url}"

        if site_url and not requires_message_action_clean(query):
            return self._find_and_open_best_site_result(query, trace, trace_id, session_id)

        history: list[dict[str, Any]] = []
        for step_index in range(1, 9):
            scan_resp, scan_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.scan",
                session_id=session_id,
                input_data={"limit": 40},
            )
            if scan_err is not None or not isinstance(scan_resp, dict):
                return (
                    "failed",
                    session_id,
                    self._humanize_tool_error(
                        "browser.scan",
                        scan_err or ToolsClientError("tool_error", "browser.scan failed"),
                        query,
                    ),
                )

            page = scan_resp.get("output", {})
            auth_state = page.get("auth", {}) if isinstance(page, dict) else {}
            if isinstance(auth_state, dict) and auth_state.get("required"):
                message = auth_state.get("message")
                return "failed", session_id, str(message or f"Я не могу это сделать, пока вы не авторизуетесь на сайте {site_url}.")

            try:
                plan = self._llm.plan_browser_step(query, page, history)
                self._append_trace(trace, f"browser_agent_step_{step_index}", "ok", "llm.browser.plan")
            except LLMClientError as exc:
                self._append_trace(
                    trace,
                    f"browser_agent_step_{step_index}",
                    "failed",
                    "llm.browser.plan",
                    detail=f"{exc.category}: {exc.message}",
                )
                return "failed", session_id, "Не удалось построить следующий шаг браузерного сценария."

            status = str(plan.get("status", "blocked"))
            message = str(plan.get("message", "")).strip() or None
            action = plan.get("action")

            history.append(
                {
                    "step": step_index,
                    "planner_status": status,
                    "planner_message": message,
                    "planner_action": action,
                    "page_url": page.get("url") if isinstance(page, dict) else None,
                }
            )

            if status == "done":
                return "done", session_id, message

            if status == "blocked":
                return "failed", session_id, message or "Браузерный сценарий остановлен планировщиком."

            if not isinstance(action, dict):
                return "failed", session_id, "LLM не вернула корректное действие для браузера."

            tool = str(action.get("tool", ""))
            input_data = action.get("input")
            if tool != "browser.act" or not isinstance(input_data, dict):
                return "failed", session_id, "LLM вернула неподдерживаемое браузерное действие."

            act_resp, act_err = self._call_tool_with_error(
                trace=trace,
                trace_id=trace_id,
                tool="browser.act",
                session_id=session_id,
                input_data=input_data,
            )
            if act_err is not None or not isinstance(act_resp, dict):
                return (
                    "failed",
                    session_id,
                    self._humanize_tool_error(
                        "browser.act",
                        act_err or ToolsClientError("tool_error", "browser.act failed"),
                        query,
                    ),
                )

            history.append(
                {
                    "step": step_index,
                    "action_result": act_resp.get("output", {}),
                }
            )

        return "failed", session_id, "Не удалось завершить браузерный сценарий за допустимое число шагов."

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
            if task.error:
                return task.error
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
        lowered = query.lower()
        site_url = resolve_site_url(query) or infer_site_url(query)
        wants_message = requires_message_action_clean(query)
        wants_news = wants_news_search(query)
        wants_product = wants_product_search_clean(query)
        open_best_result = should_open_product_result(query, site_url) if wants_product else bool(
            re.search(r"\b(открой|перейди|покажи|open|go to)\b", lowered)
        )
        looks_bulk = wants_message and bool(
            re.search(r"\b(\d+(-м)?|нескольк|последн|первым|пяти|трём|трем|three|five)\b", lowered)
        )

        if site_url and is_open_site_request_clean(query) and not wants_product and not wants_message:
            intent = "open_site"
        elif wants_message and looks_bulk:
            intent = "bulk_message"
        elif wants_message:
            intent = "send_message"
        elif wants_news:
            intent = "news_summary"
        elif wants_product:
            intent = "find_product"
        elif requires_browser_action_clean(query) or site_url:
            intent = "browser_action_generic"
        else:
            intent = "general_answer"

        request_route = "informational_request"
        if intent in {"open_site", "send_message", "bulk_message", "browser_action_generic"}:
            request_route = "browser_action_request"
        elif intent == "find_product" and open_best_result:
            request_route = "browser_action_request"

        return {
            "intent": intent,
            "entity": {
                "type": (
                    "message_target"
                    if intent in {"send_message", "bulk_message"}
                    else "news"
                    if intent == "news_summary"
                    else "product"
                    if intent == "find_product"
                    else "site"
                    if intent == "open_site"
                    else "browser_goal"
                ),
                "query": query,
            },
            "filters": {
                "condition": "new" if re.search(r"\b(новый|новая|новое|new)\b", lowered) else None,
                "sources": ["all"] if "всех" in lowered or "all" in lowered else [],
            },
            "attributes": {},
            "ranking": {
                "primary": "relevance",
                "secondary": "price_asc" if wants_product and prefers_lowest_price_product(query, site_url) else None,
            },
            "action": {
                "open_best_result": open_best_result,
            },
            "wants_product": wants_product,
            "wants_news": wants_news,
            "wants_message": wants_message,
            "request_route": request_route,
            "search_query": query,
            "news_topic": query,
            "destination_hint": extract_message_destination(query),
            "message_text": extract_message_text(query),
            "site_url": site_url,
        }

    def _normalize_intent_plan(self, query: str, plan: dict[str, Any]) -> dict[str, Any]:
        fallback = self._fallback_plan(query)
        intent = str(plan.get("intent") or fallback["intent"]).strip()
        allowed_intents = {
            "open_site",
            "find_product",
            "news_summary",
            "send_message",
            "bulk_message",
            "browser_action_generic",
            "general_answer",
        }
        if intent not in allowed_intents:
            intent = fallback["intent"]

        entity = plan.get("entity")
        filters = plan.get("filters")
        attributes = plan.get("attributes")
        ranking = plan.get("ranking")
        action = plan.get("action")
        request_route = str(plan.get("request_route") or fallback["request_route"]).strip()
        if request_route not in {"informational_request", "browser_action_request"}:
            request_route = fallback["request_route"]

        site_url = str(plan.get("site_url") or fallback["site_url"] or "").strip()
        if not site_url:
            site_url = resolve_site_url(query) or fallback["site_url"] or ""

        search_query = str(plan.get("search_query") or "").strip()
        news_topic = str(plan.get("news_topic") or "").strip()
        destination_hint = str(plan.get("destination_hint") or "").strip()
        message_text = str(plan.get("message_text") or "").strip()

        normalized = {
            "intent": intent,
            "entity": entity if isinstance(entity, dict) else fallback["entity"],
            "filters": filters if isinstance(filters, dict) else fallback["filters"],
            "attributes": attributes if isinstance(attributes, dict) else fallback["attributes"],
            "ranking": ranking if isinstance(ranking, dict) else fallback["ranking"],
            "action": action if isinstance(action, dict) else fallback["action"],
            "request_route": request_route,
            "search_query": search_query or fallback["search_query"],
            "news_topic": news_topic or fallback["news_topic"],
            "destination_hint": destination_hint or fallback["destination_hint"],
            "message_text": message_text or fallback["message_text"],
            "site_url": site_url,
        }

        if intent == "news_summary" and not is_open_site_request_clean(query):
            normalized["request_route"] = "informational_request"

        if intent == "find_product":
            action_payload = normalized["action"] if isinstance(normalized["action"], dict) else {}
            if should_open_product_result(query, site_url):
                action_payload["open_best_result"] = True
                normalized["request_route"] = "browser_action_request"
            normalized["action"] = action_payload

            ranking_payload = normalized["ranking"] if isinstance(normalized["ranking"], dict) else {}
            if prefers_lowest_price_product(query, site_url):
                ranking_payload["secondary"] = "price_asc"
            normalized["ranking"] = ranking_payload

        normalized["wants_product"] = intent == "find_product"
        normalized["wants_news"] = intent == "news_summary"
        normalized["wants_message"] = intent in {"send_message", "bulk_message"}
        normalized["supports_browser_action"] = normalized["request_route"] == "browser_action_request"
        return normalized

    def _plan_query(self, query: str, trace: list[TraceItem]) -> dict[str, Any]:
        fallback = self._fallback_plan(query)
        plan = fallback

        if self._llm is not None and self._llm.enabled():
            try:
                intent_plan = self._llm.plan_intent(query)
                plan = self._normalize_intent_plan(query, intent_plan)
                self._append_trace(
                    trace,
                    "intent_plan_ok",
                    "ok",
                    "llm.intent",
                    detail=str(plan.get("intent", "")),
                )
            except LLMClientError as exc:
                self._append_trace(
                    trace,
                    "intent_plan_failed",
                    "fallback",
                    "llm.intent",
                    detail=f"{exc.category}: {exc.message}",
                )

        self._append_trace(
            trace,
            plan["request_route"],
            "ok",
            "router",
            detail=str(plan.get("intent", "")),
        )
        has_supported_work = bool(
            plan["wants_product"] or plan["wants_news"] or plan["supports_browser_action"]
        )
        if has_supported_work:
            self._append_trace(trace, "rule_plan_ok", "ok", "rule.plan")
        else:
            self._append_trace(trace, "rule_plan_general", "ok", "rule.plan")
        return plan

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
        request_route = str(plan.get("request_route", "informational_request"))
        supports_browser_action = bool(plan.get("supports_browser_action", request_route == "browser_action_request"))
        has_supported_work = bool(plan["wants_product"] or plan["wants_news"] or supports_browser_action)
        has_search_work = bool(plan["wants_product"] or plan["wants_news"])

        result = self._empty_result()

        status = "running"
        task_error: str | None = None

        if request_route == "browser_action_request" and not supports_browser_action:
            status = "failed"
            task_error = "Запрос распознан как действие в браузере, но этот тип действия пока не поддержан."
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
        elif not has_supported_work:
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

        if plan["wants_news"]:
            news_error = self._run_non_browser_news_retrieval(req.query, trace, result)
            if news_error is not None:
                task_error = news_error

        informational_needs_browser = plan["wants_product"] or (plan["wants_news"] and not result.news)
        if informational_needs_browser:
            session_id, informational_error = self._run_informational_retrieval(
                query=req.query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                plan=plan,
                result=result,
            )
            if informational_error is not None:
                task_error = informational_error
            elif result.news or self._has_product_data(result.product):
                task_error = None

        if req.allow_social_actions and supports_browser_action and plan["wants_message"]:
            status, session_id, task_error = self._prepare_message_action(
                query=req.query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                result=result,
                plan=plan,
            )
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
        request_route = str(plan.get("request_route", "informational_request"))
        supports_browser_action = bool(plan.get("supports_browser_action", request_route == "browser_action_request"))
        has_supported_work = bool(plan["wants_product"] or plan["wants_news"] or supports_browser_action)
        has_search_work = bool(plan["wants_product"] or plan["wants_news"])
        result = self._empty_result()
        task_error: str | None = None
        status = "running"

        if request_route == "browser_action_request" and not supports_browser_action:
            task.status = "failed"
            task.result = result
            task.error = "Я понял, что это запрос на действие в браузере, но этот тип действия пока не поддержан."
            task.trace = trace
            if conversation_id is not None:
                self._set_assistant_message_for_task(
                    conversation_id,
                    task.task_id,
                    task.error,
                )
            with self._lock:
                self._tasks[task_id] = task
            return task

        if request_route == "browser_action_request":
            if plan["wants_message"]:
                status, session_id, task_error = self._prepare_message_action(
                    query=req.query,
                    trace=trace,
                    trace_id=trace_id,
                    session_id=session_id,
                    result=result,
                    plan=plan,
                )
            else:
                status, session_id, task_error = self._run_browser_action_agent_v2(
                    query=req.query,
                    trace=trace,
                    trace_id=trace_id,
                    session_id=session_id,
                    result=result,
                )
            task.status = status
            task.result = result
            task.error = task_error
            task.session_id = session_id
            task.trace = trace
            if conversation_id is not None:
                self._set_assistant_message_for_task(
                    conversation_id,
                    task.task_id,
                    task_error or self._build_assistant_text(req.query, task),
                )
            with self._lock:
                self._tasks[task_id] = task
            return task

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

        if plan["wants_news"]:
            news_error = self._run_non_browser_news_retrieval(req.query, trace, result)
            if news_error is not None:
                task_error = news_error

        informational_needs_browser = plan["wants_product"] or (plan["wants_news"] and not result.news)
        if informational_needs_browser:
            session_id, informational_error = self._run_informational_retrieval(
                query=req.query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                plan=plan,
                result=result,
            )
            if informational_error is not None and task_error is None:
                task_error = informational_error
            elif informational_error is None and (result.news or self._has_product_data(result.product)):
                task_error = None

        if req.allow_social_actions and supports_browser_action and plan["wants_message"]:
            status, session_id, task_error = self._prepare_message_action(
                query=req.query,
                trace=trace,
                trace_id=trace_id,
                session_id=session_id,
                result=result,
                plan=plan,
            )
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

    def process_task_safe(self, task_id: str, req: TaskCreateRequest) -> TaskResponse | None:
        try:
            return self.process_task(task_id, req)
        except Exception as exc:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    return None
                task = task.model_copy(deep=True)

            trace = task.trace
            self._append_trace(
                trace,
                "task_processing_failed",
                "failed",
                "backend.process_task",
                detail=f"{type(exc).__name__}: {exc}",
            )

            task.status = "failed"
            task.error = f"Внутренняя ошибка backend: {type(exc).__name__}: {exc}"
            task.trace = trace

            if task.conversation_id is not None:
                self._set_assistant_message_for_task(
                    task.conversation_id,
                    task.task_id,
                    task.error,
                )

            with self._lock:
                self._tasks[task_id] = task

            print("process_task_safe failed:")
            print(traceback.format_exc())
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

                send_resp, send_err = self._call_tool_with_error(
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
                    failure_query = ""
                    if isinstance(action.payload, dict):
                        failure_query = str(
                            action.payload.get("site_url")
                            or action.payload.get("message_text")
                            or action.payload.get("destination_hint")
                            or ""
                        )
                    task.error = self._humanize_tool_error(
                        "browser.message.send",
                        send_err or ToolsClientError("tool_error", "Message send failed"),
                        failure_query,
                    )
                    self._set_assistant_message_for_task(
                        task.conversation_id,
                        task.task_id,
                        task.error or "Не удалось отправить сообщение. Повторите позже.",
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
