"""Stable MCP contracts and domain result types."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, StrictInt

NEWS_CHANNELS = (
    "wublockchainenglish",
    "watcherguru",
)
WHALE_ALERT_CHANNEL = "whale_alert_io"
ALLOWED_TELEGRAM_CHANNELS = frozenset((*NEWS_CHANNELS, WHALE_ALERT_CHANNEL))

WHALE_ALERT_MAX_CHARS = 300
MARKET_LABEL_MAX_CHARS = 100
COIN_NAME_MAX_CHARS = 120
COIN_SYMBOL_MAX_CHARS = 20
HOMEPAGE_MAX_CHARS = 500
COIN_ID_MAX_CHARS = 200
NEWS_PREVIEW_MAX_CHARS = 150
NEWS_LOOKBACK_MIN_HOURS = 1
NEWS_LOOKBACK_MAX_HOURS = 72

COIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
COIN_ID_JSON_SCHEMA = {
    "minLength": 1,
    "maxLength": COIN_ID_MAX_CHARS,
    "pattern": COIN_ID_PATTERN.pattern,
}

TELEGRAM_CHANNEL_JSON_SCHEMA: dict[str, Any] = {"enum": sorted(ALLOWED_TELEGRAM_CHANNELS)}

TelegramChannel = Annotated[
    str,
    Field(json_schema_extra=TELEGRAM_CHANNEL_JSON_SCHEMA),
]
CoinGeckoId = Annotated[
    str,
    Field(json_schema_extra=COIN_ID_JSON_SCHEMA),
]
NewsHours = Annotated[
    StrictInt,
    Field(
        json_schema_extra={
            "minimum": NEWS_LOOKBACK_MIN_HOURS,
            "maximum": NEWS_LOOKBACK_MAX_HOURS,
        }
    ),
]
TelegramMessageId = Annotated[
    StrictInt,
    Field(json_schema_extra={"minimum": 1}),
]


class ToolErrorCode(StrEnum):
    COIN_ID_REQUIRED = "coin_id_required"
    COIN_ID_INVALID = "coin_id_invalid"
    COINGECKO_API_KEY_MISSING = "coingecko_api_key_missing"
    COIN_NOT_FOUND = "coin_not_found"
    COINGECKO_UPSTREAM_ERROR = "coingecko_upstream_error"
    NEWS_HOURS_INVALID = "news_hours_invalid"
    TELEGRAM_UNAVAILABLE = "telegram_unavailable"
    TELEGRAM_CHANNEL_NOT_ALLOWED = "telegram_channel_not_allowed"
    TELEGRAM_MESSAGE_ID_INVALID = "telegram_message_id_invalid"
    TELEGRAM_MESSAGE_NOT_FOUND = "telegram_message_not_found"
    TELEGRAM_MESSAGE_NOT_TEXT = "telegram_message_not_text"
    TELEGRAM_TIMEOUT = "telegram_timeout"
    TELEGRAM_UPSTREAM_ERROR = "telegram_upstream_error"


class CryptoToolError(ToolError):
    """Tool error with a stable machine-readable prefix."""

    def __init__(self, code: ToolErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


class TelegramAvailability(StrEnum):
    NOT_CONFIGURED = "not_configured"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"


class TelegramFetchStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    FETCH_FAILED = "fetch_failed"
    NO_MESSAGES = "no_messages"
    OK = "ok"


class MarketSourceStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    OK = "ok"


class NewsChannelFailureCode(StrEnum):
    INVALID_MESSAGE_REFERENCE = "invalid_message_reference"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"


class NewsPreview(BaseModel):
    channel: str
    message_id: int
    timestamp: str
    preview: str
    truncated: bool


class NewsChannelFailure(BaseModel):
    channel: str
    code: NewsChannelFailureCode
    message: str


class RealtimeNewsOutput(BaseModel):
    hours: int
    messages: list[NewsPreview]
    failed_channels: list[NewsChannelFailure]


@dataclass(frozen=True, slots=True)
class WhaleAlertResult:
    status: TelegramFetchStatus
    messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSourceResult:
    status: MarketSourceStatus
    payload: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class NewsMessage:
    date: datetime
    preview: NewsPreview


@dataclass(frozen=True, slots=True)
class NewsChannelResult:
    channel: str
    messages: tuple[NewsMessage, ...]
    failure: NewsChannelFailureCode | None = None
