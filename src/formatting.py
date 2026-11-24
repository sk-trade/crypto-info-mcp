"""Formatting helpers for bounded, user-facing tool responses."""

from collections.abc import Mapping

from src.contracts import (
    COIN_NAME_MAX_CHARS,
    COIN_SYMBOL_MAX_CHARS,
    HOMEPAGE_MAX_CHARS,
)


def bounded_text(value: object, max_chars: int, default: str = "N/A") -> str:
    """Return a single-line scalar bounded to ``max_chars`` characters."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return default

    text = str(value).replace("\n", " ").strip()
    if not text:
        return default
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3].rstrip()}..."


def format_percentage(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.1f}%"
    return "N/A"


def _market_cap_rank_text(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return f"{value}위"
    return "N/A"


def _krw_price_text(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"₩{value:,}"
    return "N/A"


def _first_homepage_url(links: object) -> str:
    if not isinstance(links, Mapping):
        return "N/A"

    candidates = links.get("homepage")
    if not isinstance(candidates, list):
        return "N/A"

    for candidate in candidates:
        url = bounded_text(candidate, HOMEPAGE_MAX_CHARS, default="")
        if url:
            return url
    return "N/A"


def format_coin_details(details: Mapping[str, object]) -> str:
    market_data = details.get("market_data")
    current_prices = market_data.get("current_price") if isinstance(market_data, Mapping) else None
    price_krw = current_prices.get("krw") if isinstance(current_prices, Mapping) else None

    name = bounded_text(details.get("name"), COIN_NAME_MAX_CHARS)
    symbol = bounded_text(details.get("symbol"), COIN_SYMBOL_MAX_CHARS).upper()
    rank = _market_cap_rank_text(details.get("market_cap_rank"))
    homepage = _first_homepage_url(details.get("links"))

    return "\n".join(
        (
            f"'{name}' ({symbol}) 상세 정보:",
            f"- 시가총액 순위: {rank}",
            f"- 현재 가격: {_krw_price_text(price_krw)}",
            f"- 홈페이지: {homepage}",
        )
    )
