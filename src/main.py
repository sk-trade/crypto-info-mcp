"""FastMCP entry point for read-only cryptocurrency market context."""

import asyncio
import logging
import os
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from telethon import TelegramClient
from telethon.sessions import StringSession

from src.contracts import (
    ALLOWED_TELEGRAM_CHANNELS,
    COIN_ID_JSON_SCHEMA,
    COIN_ID_MAX_CHARS,
    COIN_ID_PATTERN,
    MARKET_LABEL_MAX_CHARS,
    NEWS_CHANNELS,
    NEWS_LOOKBACK_MAX_HOURS,
    NEWS_LOOKBACK_MIN_HOURS,
    NEWS_PREVIEW_MAX_CHARS,
    WHALE_ALERT_CHANNEL,
    WHALE_ALERT_MAX_CHARS,
    CoinGeckoId,
    CryptoToolError,
    MarketSourceResult,
    MarketSourceStatus,
    NewsChannelFailure,
    NewsChannelFailureCode,
    NewsChannelResult,
    NewsHours,
    NewsMessage,
    NewsPreview,
    RealtimeNewsOutput,
    TelegramAvailability,
    TelegramChannel,
    TelegramFetchStatus,
    TelegramMessageId,
    ToolErrorCode,
    WhaleAlertResult,
)
from src.formatting import (
    bounded_text as _bounded_text,
)
from src.formatting import (
    format_coin_details as _format_coin_details,
)
from src.formatting import (
    format_percentage as _format_percentage,
)

load_dotenv()

logger = logging.getLogger(__name__)

ALTERNATIVE_ME_FEAR_AND_GREED_URL = "https://api.alternative.me/fng/?limit=1"
COINGECKO_GLOBAL_MARKET_URL = "https://api.coingecko.com/api/v3/global"
COINGECKO_COIN_DETAILS_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}"
COINGECKO_NOT_FOUND_STATUS = 404
HTTP_REQUEST_TIMEOUT_SECONDS = 10.0
TELEGRAM_OPERATION_TIMEOUT_SECONDS = 10
TELEGRAM_CLEANUP_TIMEOUT_SECONDS = 5
TELEGRAM_NEWS_MESSAGE_LIMIT = 10
TELEGRAM_WHALE_MESSAGE_LIMIT = 5
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8123

TELEGRAM_UNAVAILABLE_MESSAGE = (
    "텔레그램을 사용할 수 없습니다. 서버 운영자는 TELEGRAM_API_ID, "
    "TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING 설정과 "
    "`uv run python scripts/generate_session.py`로 생성한 인증 세션을 확인해주세요."
)

NEWS_CHANNEL_FAILURE_MESSAGES: dict[NewsChannelFailureCode, str] = {
    NewsChannelFailureCode.INVALID_MESSAGE_REFERENCE: "메시지 참조를 확인할 수 없습니다.",
    NewsChannelFailureCode.TIMEOUT: "채널 조회 시간이 초과되었습니다.",
    NewsChannelFailureCode.UPSTREAM_ERROR: "채널을 조회할 수 없습니다.",
}

WHALE_ALERT_STATUS_MESSAGES: dict[TelegramFetchStatus, str] = {
    TelegramFetchStatus.FETCH_FAILED: "- 주요 자금 이동: Telegram 조회 실패로 확인 불가",
    TelegramFetchStatus.UNAUTHORIZED: "- 주요 자금 이동: Telegram 인증에 실패하여 확인 불가",
    TelegramFetchStatus.UNAVAILABLE: "- 주요 자금 이동: Telegram을 사용할 수 없어 확인 불가",
    TelegramFetchStatus.NOT_CONFIGURED: "- 주요 자금 이동: Telegram이 설정되지 않아 확인 불가",
    TelegramFetchStatus.NO_MESSAGES: "- 주요 자금 이동: 최근 1시간 내 포착된 움직임 없음",
}


def _read_optional_int_env(name: str) -> int | None:
    raw_value = os.getenv(name)
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        logger.warning("%s must be an integer; the integration is disabled.", name)
        return None


COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
TELEGRAM_API_ID = _read_optional_int_env("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")

telegram_client_state: TelegramClient | TelegramAvailability = (
    TelegramAvailability.UNAVAILABLE
    if all((TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING))
    else TelegramAvailability.NOT_CONFIGURED
)
upstream_http_client: httpx.AsyncClient | None = None


async def _disconnect_telegram_client(telegram_client: TelegramClient | None) -> bool:
    if telegram_client is None or not telegram_client.is_connected():
        return False

    try:
        async with asyncio.timeout(TELEGRAM_CLEANUP_TIMEOUT_SECONDS):
            await cast(Awaitable[object], telegram_client.disconnect())
    except TimeoutError:
        logger.warning("Telegram client cleanup timed out.")
        return False
    except Exception:
        logger.exception("Telegram client cleanup failed.")
        return False
    return True


async def _connect_telegram_client() -> TelegramClient | TelegramAvailability:
    telegram_api_id = TELEGRAM_API_ID
    telegram_api_hash = TELEGRAM_API_HASH
    telegram_session_string = TELEGRAM_SESSION_STRING
    if not telegram_api_id or not telegram_api_hash or not telegram_session_string:
        logger.info("Telegram is not configured; Telegram-backed features are disabled.")
        return TelegramAvailability.NOT_CONFIGURED

    telegram_client: TelegramClient | None = None
    try:
        logger.info("Connecting to Telegram.")
        telegram_client = TelegramClient(
            StringSession(telegram_session_string),
            telegram_api_id,
            telegram_api_hash,
        )
        async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
            await telegram_client.connect()
            is_authorized = await telegram_client.is_user_authorized()

        if is_authorized:
            logger.info("Telegram client connected.")
            return telegram_client

        logger.warning("Telegram authorization failed; regenerate TELEGRAM_SESSION_STRING.")
        await _disconnect_telegram_client(telegram_client)
        return TelegramAvailability.UNAUTHORIZED
    except TimeoutError:
        logger.warning("Telegram initialization timed out.")
        await _disconnect_telegram_client(telegram_client)
    except Exception:
        logger.exception("Telegram initialization failed.")
        await _disconnect_telegram_client(telegram_client)
    return TelegramAvailability.UNAVAILABLE


@asynccontextmanager
async def _http_client_context():
    """Reuse the server HTTP client, with a transient client for direct calls."""
    if upstream_http_client is not None:
        yield upstream_http_client
        return

    async with httpx.AsyncClient() as transient_http_client:
        yield transient_http_client


@asynccontextmanager
async def lifespan(_app: FastMCP):
    """Own shared HTTP and optional Telegram clients for the server lifetime."""
    global telegram_client_state, upstream_http_client

    async with httpx.AsyncClient() as shared_http_client:
        upstream_http_client = shared_http_client
        telegram_client_state = await _connect_telegram_client()
        try:
            yield
        finally:
            active_telegram_client = telegram_client_state
            if not isinstance(active_telegram_client, TelegramAvailability):
                telegram_client_state = TelegramAvailability.UNAVAILABLE
                if await _disconnect_telegram_client(active_telegram_client):
                    logger.info("Telegram client disconnected.")
            upstream_http_client = None


mcp = FastMCP("crypto-info-mcp", lifespan=lifespan)


def _get_telegram_client() -> TelegramClient:
    if isinstance(telegram_client_state, TelegramAvailability):
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_UNAVAILABLE,
            TELEGRAM_UNAVAILABLE_MESSAGE,
        )
    return telegram_client_state


def _parse_fear_and_greed_payload(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None

    records = payload.get("data")
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return None

    latest = records[0]
    raw_value = latest.get("value")
    classification = latest.get("value_classification")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int, float)):
        return None

    try:
        index_value = float(raw_value)
    except (TypeError, ValueError):
        return None

    if not 0 <= index_value <= 100:
        return None
    if not isinstance(classification, str) or not classification.strip():
        return None
    return latest


async def _fetch_fear_and_greed_index() -> MarketSourceResult:
    """Fetch the current Alternative.me Fear & Greed record."""
    try:
        async with _http_client_context() as http_client:
            sentiment_response = await http_client.get(
                ALTERNATIVE_ME_FEAR_AND_GREED_URL,
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            sentiment_response.raise_for_status()
            latest = _parse_fear_and_greed_payload(sentiment_response.json())
    except Exception as exc:
        logger.warning("Alternative.me market sentiment request failed: %s", exc)
        return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)

    if latest is None:
        logger.warning("Alternative.me returned an unexpected response shape.")
        return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)
    return MarketSourceResult(MarketSourceStatus.OK, latest)


def _parse_global_market_payload(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None

    market_data = payload.get("data")
    if not isinstance(market_data, dict):
        return None

    percentages = market_data.get("market_cap_percentage")
    if not isinstance(percentages, dict) or not {"btc", "eth"} <= percentages.keys():
        return None
    return market_data


async def _fetch_global_market_data() -> MarketSourceResult:
    """Fetch CoinGecko global market dominance data."""
    coingecko_api_key = COINGECKO_API_KEY
    if not coingecko_api_key:
        return MarketSourceResult(MarketSourceStatus.NOT_CONFIGURED)

    try:
        async with _http_client_context() as http_client:
            market_response = await http_client.get(
                COINGECKO_GLOBAL_MARKET_URL,
                headers={"x-cg-demo-api-key": coingecko_api_key},
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            market_response.raise_for_status()
            market_data = _parse_global_market_payload(market_response.json())
    except Exception as exc:
        logger.warning("CoinGecko global market request failed: %s", exc)
        return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)

    if market_data is None:
        logger.warning("CoinGecko global endpoint returned an unexpected response shape.")
        return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)
    return MarketSourceResult(MarketSourceStatus.OK, market_data)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_before_since(message: object, since: datetime) -> bool:
    message_date = getattr(message, "date", None)
    return isinstance(message_date, datetime) and _as_utc(message_date) < since


async def _fetch_whale_alerts() -> WhaleAlertResult:
    """Fetch whale-alert messages from the previous hour."""
    current_telegram_state = telegram_client_state
    unavailable_status = {
        TelegramAvailability.NOT_CONFIGURED: TelegramFetchStatus.NOT_CONFIGURED,
        TelegramAvailability.UNAUTHORIZED: TelegramFetchStatus.UNAUTHORIZED,
        TelegramAvailability.UNAVAILABLE: TelegramFetchStatus.UNAVAILABLE,
    }
    if isinstance(current_telegram_state, TelegramAvailability):
        return WhaleAlertResult(unavailable_status[current_telegram_state])

    since = datetime.now(UTC) - timedelta(hours=1)
    messages: list[str] = []
    try:
        async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
            async for message in current_telegram_state.iter_messages(
                WHALE_ALERT_CHANNEL,
                limit=TELEGRAM_WHALE_MESSAGE_LIMIT,
            ):
                if _is_before_since(message, since):
                    break
                text = getattr(message, "text", None)
                if isinstance(text, str) and text:
                    messages.append(text)
    except TimeoutError:
        logger.warning("Whale-alert channel request timed out.")
        return WhaleAlertResult(TelegramFetchStatus.FETCH_FAILED)
    except Exception as exc:
        logger.warning("Whale-alert channel request failed: %s", exc)
        return WhaleAlertResult(TelegramFetchStatus.FETCH_FAILED)

    if not messages:
        return WhaleAlertResult(TelegramFetchStatus.NO_MESSAGES)
    return WhaleAlertResult(TelegramFetchStatus.OK, tuple(messages))


def _news_message_from_telegram(
    channel: str,
    message: object,
    fallback_date: datetime,
) -> NewsMessage | None:
    message_id = getattr(message, "id", None)
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id < 1:
        return None

    text = getattr(message, "text", None)
    if not isinstance(text, str) or not text:
        return None

    raw_date = getattr(message, "date", None)
    message_date = _as_utc(raw_date) if isinstance(raw_date, datetime) else fallback_date
    normalized_text = text.replace("\n", " ").strip()
    truncated = len(normalized_text) > NEWS_PREVIEW_MAX_CHARS
    preview = (
        f"{normalized_text[: NEWS_PREVIEW_MAX_CHARS - 3]}..." if truncated else normalized_text
    )
    return NewsMessage(
        date=message_date,
        preview=NewsPreview(
            channel=channel,
            message_id=message_id,
            timestamp=message_date.strftime("%m-%d %H:%M UTC"),
            preview=preview,
            truncated=truncated,
        ),
    )


async def _fetch_news_channel(
    client: TelegramClient,
    channel: str,
    since: datetime,
) -> NewsChannelResult:
    messages: list[NewsMessage] = []
    failure: NewsChannelFailureCode | None = None
    try:
        async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
            async for message in client.iter_messages(
                channel,
                limit=TELEGRAM_NEWS_MESSAGE_LIMIT,
            ):
                if _is_before_since(message, since):
                    break
                if not getattr(message, "text", None):
                    continue

                news_message = _news_message_from_telegram(channel, message, since)
                if news_message is None:
                    failure = NewsChannelFailureCode.INVALID_MESSAGE_REFERENCE
                    continue
                messages.append(news_message)
    except TimeoutError:
        logger.warning("Telegram news request timed out for %s.", channel)
        failure = NewsChannelFailureCode.TIMEOUT
    except Exception as exc:
        logger.warning("Telegram news request failed for %s: %s", channel, exc)
        failure = NewsChannelFailureCode.UPSTREAM_ERROR
    return NewsChannelResult(channel, tuple(messages), failure)


def _market_result_or_unavailable(value: object) -> MarketSourceResult:
    if isinstance(value, MarketSourceResult):
        return value
    return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)


def _whale_result_or_failed(value: object) -> WhaleAlertResult:
    if isinstance(value, WhaleAlertResult):
        return value
    return WhaleAlertResult(TelegramFetchStatus.FETCH_FAILED)


def _sentiment_report_line(result: MarketSourceResult) -> str:
    if result.status is not MarketSourceStatus.OK or not isinstance(result.payload, dict):
        return "- 시장 심리: Alternative.me 조회 실패로 확인 불가"

    classification = _bounded_text(
        result.payload.get("value_classification"),
        MARKET_LABEL_MAX_CHARS,
    )
    index_value = _bounded_text(result.payload.get("value"), MARKET_LABEL_MAX_CHARS)
    return f"- 시장 심리: '{classification}' (지수: {index_value})"


def _dominance_report_line(result: MarketSourceResult) -> str | None:
    if result.status is MarketSourceStatus.NOT_CONFIGURED:
        return None
    if result.status is not MarketSourceStatus.OK or not isinstance(result.payload, dict):
        return "- 시장 지배력: CoinGecko 조회 실패로 확인 불가"

    percentages = result.payload.get("market_cap_percentage")
    if not isinstance(percentages, dict):
        return "- 시장 지배력: CoinGecko 조회 실패로 확인 불가"

    btc_dominance = _format_percentage(percentages.get("btc"))
    eth_dominance = _format_percentage(percentages.get("eth"))
    return f"- 시장 지배력: BTC {btc_dominance}, ETH {eth_dominance}"


def _whale_alert_report_lines(result: WhaleAlertResult) -> list[str]:
    if result.status is TelegramFetchStatus.OK and result.messages:
        return [
            "- 주요 자금 이동 (지난 1시간):",
            *(
                f"  - {_bounded_text(message, WHALE_ALERT_MAX_CHARS, default='')}"
                for message in result.messages
            ),
        ]

    fallback = WHALE_ALERT_STATUS_MESSAGES.get(result.status)
    return [fallback or "- 주요 자금 이동: 포착된 움직임 없음"]


@mcp.tool()
async def get_market_overview() -> str:
    """시장 심리, BTC/ETH 지배력, 최근 고래 알림을 요약합니다."""
    raw_sentiment, raw_global_market, raw_whale_alerts = await asyncio.gather(
        _fetch_fear_and_greed_index(),
        _fetch_global_market_data(),
        _fetch_whale_alerts(),
        return_exceptions=True,
    )
    sentiment = _market_result_or_unavailable(raw_sentiment)
    global_market = _market_result_or_unavailable(raw_global_market)
    whale_alerts = _whale_result_or_failed(raw_whale_alerts)

    report = ["현재 시장 개요 브리핑:", _sentiment_report_line(sentiment)]
    dominance_line = _dominance_report_line(global_market)
    if dominance_line is not None:
        report.append(dominance_line)
    report.extend(_whale_alert_report_lines(whale_alerts))
    return "\n".join(report)


def _normalize_coin_id(coin_id: str | None) -> str:
    if coin_id is None or not coin_id.strip():
        raise CryptoToolError(
            ToolErrorCode.COIN_ID_REQUIRED,
            "CoinGecko 코인 ID를 입력해주세요.",
        )

    normalized = coin_id.strip()
    if len(normalized) > COIN_ID_MAX_CHARS or not COIN_ID_PATTERN.fullmatch(normalized):
        raise CryptoToolError(
            ToolErrorCode.COIN_ID_INVALID,
            "CoinGecko 코인 ID는 영문, 숫자, 밑줄, 하이픈으로 구성된 200자 이하의 값이어야 합니다.",
        )
    return normalized


async def _request_coin_details(coin_id: str, coingecko_api_key: str) -> dict[str, object]:
    url = COINGECKO_COIN_DETAILS_URL.format(coin_id=coin_id)
    params = {
        "localization": "false",
        "tickers": "false",
        "community_data": "false",
        "developer_data": "false",
    }
    try:
        async with _http_client_context() as http_client:
            coin_response = await http_client.get(
                url,
                params=params,
                headers={"x-cg-demo-api-key": coingecko_api_key},
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            coin_response.raise_for_status()
            payload = coin_response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == COINGECKO_NOT_FOUND_STATUS:
            raise CryptoToolError(
                ToolErrorCode.COIN_NOT_FOUND,
                f"'{coin_id}' 코인을 찾을 수 없습니다. ID를 확인해주세요.",
            ) from None
        logger.warning("CoinGecko coin request failed for %s: %s", coin_id, exc)
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
            f"'{coin_id}' 정보 조회 중 CoinGecko API 오류가 발생했습니다.",
        ) from exc
    except Exception as exc:
        logger.warning("CoinGecko coin response failed for %s: %s", coin_id, exc)
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
            f"'{coin_id}' 정보를 가져오는 데 실패했습니다.",
        ) from exc

    if not isinstance(payload, dict):
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
            f"'{coin_id}' 정보 응답을 확인할 수 없습니다.",
        )
    return payload


@mcp.tool()
async def get_coin_details(coin_id: CoinGeckoId | None = None) -> str:
    """CoinGecko ID로 코인의 순위, 원화 가격, 홈페이지를 조회합니다."""
    normalized_coin_id = _normalize_coin_id(coin_id)
    coingecko_api_key = COINGECKO_API_KEY
    if not coingecko_api_key:
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_API_KEY_MISSING,
            "서버에 CoinGecko API 키(COINGECKO_API_KEY)가 설정되지 않았습니다.",
        )

    details = await _request_coin_details(normalized_coin_id, coingecko_api_key)
    name = details.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.warning(
            "CoinGecko returned an unidentifiable payload for %s.",
            normalized_coin_id,
        )
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
            f"'{normalized_coin_id}' 정보 응답을 확인할 수 없습니다.",
        )
    return _format_coin_details(details)


# FastMCP validates required arguments before tool code. The runtime default still
# allows direct calls without an argument to return the domain-specific error.
get_coin_details.parameters["properties"]["coin_id"] = {
    "type": "string",
    **COIN_ID_JSON_SCHEMA,
}
get_coin_details.parameters["required"] = ["coin_id"]


def _validate_news_hours(hours: int) -> None:
    if (
        not isinstance(hours, int)
        or isinstance(hours, bool)
        or not NEWS_LOOKBACK_MIN_HOURS <= hours <= NEWS_LOOKBACK_MAX_HOURS
    ):
        raise CryptoToolError(
            ToolErrorCode.NEWS_HOURS_INVALID,
            f"'hours' 파라미터는 {NEWS_LOOKBACK_MIN_HOURS}과 "
            f"{NEWS_LOOKBACK_MAX_HOURS} 사이의 값이어야 합니다.",
        )


def _merge_news_results(
    results: list[NewsChannelResult],
) -> tuple[list[NewsMessage], dict[str, NewsChannelFailureCode]]:
    messages = [message for result in results for message in result.messages]
    messages.sort(key=lambda message: message.date, reverse=True)
    failed_channels = {
        result.channel: result.failure for result in results if result.failure is not None
    }
    return messages, failed_channels


def _raise_for_total_news_failure(
    messages: list[NewsMessage],
    failed_channels: dict[str, NewsChannelFailureCode],
) -> None:
    if messages or set(failed_channels) != set(NEWS_CHANNELS):
        return

    if all(failure is NewsChannelFailureCode.TIMEOUT for failure in failed_channels.values()):
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_TIMEOUT,
            "모든 Telegram 뉴스 채널 조회 시간이 초과되었습니다.",
        )
    raise CryptoToolError(
        ToolErrorCode.TELEGRAM_UPSTREAM_ERROR,
        "모든 Telegram 뉴스 채널을 조회하는 데 실패했습니다.",
    )


def _news_report_lines(
    hours: int,
    messages: list[NewsMessage],
    failed_channels: dict[str, NewsChannelFailureCode],
) -> list[str]:
    if messages:
        report = [f"지난 {hours}시간 동안의 주요 뉴스:"]
        report.extend(
            f"- [{message.preview.timestamp}] @{message.preview.channel} "
            f"#{message.preview.message_id} / {message.preview.preview}"
            f"{' (원문 참조 필요)' if message.preview.truncated else ''}"
            for message in messages
        )
    elif failed_channels:
        report = [f"지난 {hours}시간 동안 조회에 성공한 채널에서는 새로운 뉴스가 없습니다."]
    else:
        report = [f"지난 {hours}시간 동안 지정된 채널에서 새로운 뉴스가 없습니다."]

    if failed_channels:
        report.extend(("", "조회 실패 채널:"))
        report.extend(
            f"  @{channel}: 조회 실패: {NEWS_CHANNEL_FAILURE_MESSAGES[failure]}"
            for channel, failure in failed_channels.items()
        )
    return report


def _realtime_news_output(
    hours: int,
    messages: list[NewsMessage],
    failed_channels: dict[str, NewsChannelFailureCode],
) -> RealtimeNewsOutput:
    return RealtimeNewsOutput(
        hours=hours,
        messages=[message.preview for message in messages],
        failed_channels=[
            NewsChannelFailure(
                channel=channel,
                code=failure,
                message=NEWS_CHANNEL_FAILURE_MESSAGES[failure],
            )
            for channel, failure in failed_channels.items()
        ],
    )


@mcp.tool(output_schema=RealtimeNewsOutput.model_json_schema())
async def get_realtime_news(hours: NewsHours = 1) -> ToolResult:
    """지난 1~72시간의 Telegram 뉴스 미리보기와 안정적인 참조를 반환합니다."""
    _validate_news_hours(hours)
    client = _get_telegram_client()
    since = datetime.now(UTC) - timedelta(hours=hours)

    results = await asyncio.gather(
        *(_fetch_news_channel(client, channel, since) for channel in NEWS_CHANNELS)
    )
    messages, failed_channels = _merge_news_results(results)
    _raise_for_total_news_failure(messages, failed_channels)

    output = _realtime_news_output(hours, messages, failed_channels)
    return ToolResult(
        content=[
            TextContent(
                type="text",
                text="\n".join(_news_report_lines(hours, messages, failed_channels)),
            )
        ],
        structured_content=output.model_dump(mode="json"),
    )


@mcp.tool()
async def get_telegram_message(
    channel: TelegramChannel,
    message_id: TelegramMessageId,
) -> str:
    """허용된 Telegram 채널의 특정 텍스트 메시지 원문을 조회합니다."""
    if channel not in ALLOWED_TELEGRAM_CHANNELS:
        allowed_channels = ", ".join(sorted(ALLOWED_TELEGRAM_CHANNELS))
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_CHANNEL_NOT_ALLOWED,
            f"허용되지 않은 채널입니다. 허용 채널: {allowed_channels}",
        )
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id < 1:
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_MESSAGE_ID_INVALID,
            "'message_id' 파라미터는 1 이상의 정수여야 합니다.",
        )

    client = _get_telegram_client()
    try:
        async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
            message = await client.get_messages(channel, ids=message_id)
    except TimeoutError as exc:
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_TIMEOUT,
            "Telegram 메시지 조회 시간이 초과되었습니다.",
        ) from exc
    except Exception as exc:
        logger.warning(
            "Telegram message request failed for %s#%s: %s",
            channel,
            message_id,
            exc,
        )
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_UPSTREAM_ERROR,
            "메시지 조회 중 Telegram 오류가 발생했습니다.",
        ) from exc

    if not message:
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_MESSAGE_NOT_FOUND,
            f"채널 '{channel}'에서 메시지 ID {message_id}를 찾을 수 없습니다.",
        )

    text = getattr(message, "text", None)
    if not isinstance(text, str) or not text:
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_MESSAGE_NOT_TEXT,
            f"메시지 ID {message_id}는 텍스트 콘텐츠를 포함하지 않습니다.",
        )
    return text


def _configure_logging() -> None:
    requested_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, requested_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run() -> int:
    _configure_logging()
    print("Crypto Info MCP 서버를 시작합니다.", flush=True)
    print(f"- 서버 주소: http://{SERVER_HOST}:{SERVER_PORT}", flush=True)
    print("- 종료: Ctrl+C", flush=True)

    try:
        mcp.run(
            transport="streamable-http",
            host=SERVER_HOST,
            port=SERVER_PORT,
            uvicorn_config={"timeout_graceful_shutdown": 5},
        )
    except KeyboardInterrupt:
        print("\n서버를 안전하게 종료했습니다.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
