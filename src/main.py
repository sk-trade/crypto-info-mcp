import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from pydantic import (
    BaseModel,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

# --- 설정 및 전역 변수 초기화 ---
load_dotenv()

# API 키 및 설정 로드
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")


def _get_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        print(f"{name} must be an integer.")
        return None


TELEGRAM_API_ID = _get_int_env("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")

ALLOWED_TELEGRAM_CHANNELS = {
    "wublockchainenglish",
    "watcherguru",
    "whale_alert_io",
}
TelegramChannel = Annotated[
    str,
    Field(json_schema_extra={"enum": sorted(ALLOWED_TELEGRAM_CHANNELS)}),
]
TELEGRAM_UNAVAILABLE_MESSAGE = (
    "텔레그램을 사용할 수 없습니다. 서버 운영자는 TELEGRAM_API_ID, "
    "TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING 설정과 "
    "`uv run python scripts/generate_session.py`로 생성한 인증 세션을 확인해주세요."
)
TELEGRAM_OPERATION_TIMEOUT_SECONDS = 10
TELEGRAM_CLEANUP_TIMEOUT_SECONDS = 5

WHALE_ALERT_MAX_CHARS = 300
MARKET_LABEL_MAX_CHARS = 100
COIN_NAME_MAX_CHARS = 120
COIN_SYMBOL_MAX_CHARS = 20
HOMEPAGE_MAX_CHARS = 500
COIN_ID_MAX_CHARS = 200
COIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
COIN_ID_JSON_SCHEMA = {
    "minLength": 1,
    "maxLength": COIN_ID_MAX_CHARS,
    "pattern": COIN_ID_PATTERN.pattern,
}
CoinGeckoId = Annotated[
    str,
    Field(json_schema_extra=COIN_ID_JSON_SCHEMA),
]
NewsHours = Annotated[
    StrictInt,
    Field(json_schema_extra={"minimum": 1, "maximum": 72}),
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
    def __init__(self, code: ToolErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


class TelegramAvailability(StrEnum):
    NOT_CONFIGURED = "not_configured"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


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


class CoinDetailsIdentity(BaseModel):
    name: StrictStr

    @field_validator("name")
    @classmethod
    def require_non_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("coin name must not be blank")
        return value


@dataclass(frozen=True)
class WhaleAlertResult:
    status: TelegramFetchStatus
    messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketSourceResult:
    status: MarketSourceStatus
    data: dict | None = None


@dataclass(frozen=True)
class NewsMessage:
    date: datetime
    item: NewsPreview


@dataclass(frozen=True)
class NewsChannelResult:
    channel: str
    messages: tuple[NewsMessage, ...]
    failure: NewsChannelFailureCode | None = None


NEWS_CHANNEL_FAILURE_MESSAGES: dict[NewsChannelFailureCode, str] = {
    NewsChannelFailureCode.INVALID_MESSAGE_REFERENCE: "메시지 참조를 확인할 수 없습니다.",
    NewsChannelFailureCode.TIMEOUT: "채널 조회 시간이 초과되었습니다.",
    NewsChannelFailureCode.UPSTREAM_ERROR: "채널을 조회할 수 없습니다.",
}

telegram_client: TelegramClient | None = None
telegram_availability = (
    TelegramAvailability.UNAVAILABLE
    if all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING])
    else TelegramAvailability.NOT_CONFIGURED
)


def _bounded_text(value, max_chars: int, default: str = 'N/A') -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return default
    text = str(value).replace('\n', ' ').strip()
    if not text:
        return default
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3].rstrip() + "..."


def _format_coin_details(details: dict) -> str:
    market_data = details.get('market_data') or {}
    current_price = market_data.get('current_price') if isinstance(market_data, dict) else {}
    price_krw = current_price.get('krw', 'N/A') if isinstance(current_price, dict) else 'N/A'
    if not isinstance(price_krw, (int, float)) or isinstance(price_krw, bool):
        price_krw = 'N/A'
    symbol = _bounded_text(details.get('symbol'), COIN_SYMBOL_MAX_CHARS)
    market_cap_rank = details.get('market_cap_rank')
    if not isinstance(market_cap_rank, int) or isinstance(market_cap_rank, bool) or market_cap_rank < 1:
        market_cap_rank = 'N/A'
    links = details.get('links') or {}
    homepage = links.get('homepage') if isinstance(links, dict) else None
    homepage_url = 'N/A'
    if isinstance(homepage, list):
        for candidate in homepage:
            bounded_candidate = _bounded_text(candidate, HOMEPAGE_MAX_CHARS, default='')
            if bounded_candidate:
                homepage_url = bounded_candidate
                break

    report = [
        f"'{_bounded_text(details.get('name'), COIN_NAME_MAX_CHARS)}' ({symbol.upper()}) 상세 정보:",
        f"- 시가총액 순위: {market_cap_rank}위",
        f"- 현재 가격: ₩{price_krw:,}" if isinstance(price_krw, (int, float)) else f"- 현재 가격: {price_krw}",
        f"- 홈페이지: {homepage_url}"
    ]
    return "\n".join(report)


def _format_percentage(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.1f}%"
    return "N/A"


def _format_whale_alert(value: str) -> str:
    cleaned = value.replace('\n', ' ').strip()
    if len(cleaned) <= WHALE_ALERT_MAX_CHARS:
        return cleaned
    return cleaned[:WHALE_ALERT_MAX_CHARS - 3].rstrip() + "..."


async def _disconnect_telegram_client(client):
    if not client:
        return False

    try:
        if client.is_connected():
            async with asyncio.timeout(TELEGRAM_CLEANUP_TIMEOUT_SECONDS):
                await client.disconnect()
            return True
    except TimeoutError:
        print("텔레그램 연결 해제 시간이 초과되었지만 무시합니다.")
    except Exception as e:
        print(f"텔레그램 연결 해제 중 오류가 발생했지만 무시합니다: {e}")
    return False


@asynccontextmanager
async def lifespan(app: FastMCP):
    """
    서버 시작 시 초기화된 전역 텔레그램 클라이언트 인스턴스를 반환합니다.
    """
    global telegram_client, telegram_availability
    if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING]):
        telegram_availability = TelegramAvailability.NOT_CONFIGURED
        print("텔레그램 환경 변수가 설정되지않아 관련 기능이 비활성화됩니다.")
    else:
        telegram_availability = TelegramAvailability.UNAVAILABLE
        client = None
        try:
            print("Connecting to Telegram...")
            client = TelegramClient(StringSession(TELEGRAM_SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
            async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
                await client.connect()
                is_authorized = await client.is_user_authorized()
            if not is_authorized:
                print("텔레그램 인증이 필요합니다. 로컬에서 스크립트를 실행하여 세션 파일을 생성해주세요.")
                await _disconnect_telegram_client(client)
                telegram_client = None
                telegram_availability = TelegramAvailability.UNAUTHORIZED
            else:
                telegram_client = client
                telegram_availability = TelegramAvailability.AVAILABLE
                print("텔레그램 클라이언트 연결 완료.")
        except TimeoutError:
            print("텔레그램 초기화 시간이 초과되어 관련 기능이 비활성화됩니다.")
            telegram_client = None
            telegram_availability = TelegramAvailability.UNAVAILABLE
            await _disconnect_telegram_client(client)
        except Exception as e:
            print(f"텔레그램 초기화 실패로 관련 기능이 비활성화됩니다: {e}")
            telegram_client = None
            telegram_availability = TelegramAvailability.UNAVAILABLE
            await _disconnect_telegram_client(client)
    try:
        yield
    finally:
        client = telegram_client
        telegram_client = None
        if telegram_availability is TelegramAvailability.AVAILABLE:
            telegram_availability = TelegramAvailability.UNAVAILABLE
        if client:
            print("Disconnecting from Telegram...")
            if await _disconnect_telegram_client(client):
                print("텔레그램 클라이언트 연결 해제 완료.")

# FastMCP 앱 인스턴스 생성
mcp = FastMCP("Intelligent Crypto Assistant", lifespan=lifespan)

# --- 내부 헬퍼 함수 ---

async def _get_telegram_client() -> TelegramClient:
    """
    서버 시작 시 초기화된 전역 텔레그램 클라이언트 인스턴스를 반환합니다.
    """
    if telegram_client is None:
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_UNAVAILABLE,
            TELEGRAM_UNAVAILABLE_MESSAGE,
        )
    return telegram_client

async def _fetch_fear_and_greed_index() -> MarketSourceResult:
    """alternative.me에서 최신 공포 및 탐욕 지수를 비동기적으로 가져옵니다."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data') if isinstance(payload, dict) else None
            if isinstance(data, list) and data and isinstance(data[0], dict):
                latest = data[0]
                raw_value = latest.get('value')
                classification = latest.get('value_classification')
                try:
                    index_value = float(raw_value) if not isinstance(raw_value, bool) else None
                except (TypeError, ValueError):
                    index_value = None
                if (
                    index_value is not None
                    and 0 <= index_value <= 100
                    and isinstance(classification, str)
                    and classification.strip()
                ):
                    return MarketSourceResult(MarketSourceStatus.OK, latest)
            return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)
    except Exception as e:
        print(f"Fear & Greed Index Fetch Error: {e}")
        return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)

async def _fetch_global_market_data() -> MarketSourceResult:
    """CoinGecko API에서 글로벌 마켓 데이터(예: 도미넌스)를 비동기적으로 가져옵니다."""
    if not COINGECKO_API_KEY:
        return MarketSourceResult(MarketSourceStatus.NOT_CONFIGURED)
    try:
        url = "https://api.coingecko.com/api/v3/global"
        headers = {'x-cg-demo-api-key': COINGECKO_API_KEY}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data') if isinstance(payload, dict) else None
            percentages = (
                data.get('market_cap_percentage')
                if isinstance(data, dict)
                else None
            )
            if isinstance(percentages, dict) and {'btc', 'eth'} <= percentages.keys():
                return MarketSourceResult(MarketSourceStatus.OK, data)
            return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)
    except Exception as e:
        print(f"Global Market Data Fetch Error: {e}")
        return MarketSourceResult(MarketSourceStatus.UNAVAILABLE)


def _is_before_since(message, since: datetime) -> bool:
    """Return whether a Telegram message is older than the requested UTC window."""
    message_date = getattr(message, "date", None)
    if message_date is None:
        return False
    return _as_utc(message_date) < since


def _as_utc(value: datetime) -> datetime:
    """Normalize Telegram datetimes without depending on the server timezone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _fetch_whale_alerts() -> WhaleAlertResult:
    """텔레그램 'whale_alert_io' 채널에서 지난 1시간 동안의 메시지를 가져옵니다."""
    if telegram_availability is TelegramAvailability.NOT_CONFIGURED:
        return WhaleAlertResult(TelegramFetchStatus.NOT_CONFIGURED)
    if telegram_availability is TelegramAvailability.UNAUTHORIZED:
        return WhaleAlertResult(TelegramFetchStatus.UNAUTHORIZED)
    if telegram_availability is not TelegramAvailability.AVAILABLE:
        return WhaleAlertResult(TelegramFetchStatus.UNAVAILABLE)

    try:
        client = await _get_telegram_client()
    except CryptoToolError:
        return WhaleAlertResult(TelegramFetchStatus.UNAVAILABLE)

    messages_text: list[str] = []
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        # Telethon's default iteration is newest first, so the first older post ends the window.
        async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
            async for message in client.iter_messages('whale_alert_io', limit=5):
                if _is_before_since(message, since):
                    break
                if message.text:
                    messages_text.append(message.text)
    except TimeoutError:
        print("Whale Alert fetch timed out.")
        return WhaleAlertResult(TelegramFetchStatus.FETCH_FAILED)
    except Exception as e:
        print(f"Whale Alert Fetch Error: {e}")
        return WhaleAlertResult(TelegramFetchStatus.FETCH_FAILED)
    if not messages_text:
        return WhaleAlertResult(TelegramFetchStatus.NO_MESSAGES)
    return WhaleAlertResult(TelegramFetchStatus.OK, tuple(messages_text))


async def _fetch_news_channel(
    client: TelegramClient,
    channel: str,
    since: datetime,
) -> NewsChannelResult:
    messages: list[NewsMessage] = []
    failure: NewsChannelFailureCode | None = None
    try:
        # Request newest posts first and stop at the first one outside the time window.
        async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
            async for msg in client.iter_messages(channel, limit=10):
                if _is_before_since(msg, since):
                    break
                if msg.text:
                    message_id = getattr(msg, "id", None)
                    if (
                        not isinstance(message_id, int)
                        or isinstance(message_id, bool)
                        or message_id < 1
                    ):
                        failure = NewsChannelFailureCode.INVALID_MESSAGE_REFERENCE
                        continue
                    message_date = _as_utc(msg.date) if getattr(msg, "date", None) else since
                    preview = msg.text.replace('\n', ' ').strip()
                    if len(preview) > 150:
                        preview = preview[:147] + "..."
                        truncated = True
                    else:
                        truncated = False
                    messages.append(NewsMessage(
                        date=message_date,
                        item=NewsPreview(
                            channel=channel,
                            message_id=message_id,
                            timestamp=message_date.strftime('%m-%d %H:%M UTC'),
                            preview=preview,
                            truncated=truncated,
                        ),
                    ))
    except TimeoutError:
        print(f"Telegram news fetch timed out for {channel}.")
        failure = NewsChannelFailureCode.TIMEOUT
    except Exception as e:
        print(f"Telegram news fetch error for {channel}: {e}")
        failure = NewsChannelFailureCode.UPSTREAM_ERROR
    return NewsChannelResult(channel, tuple(messages), failure)


# --- MCP 도구 함수 정의 ---

@mcp.tool()
async def get_market_overview() -> str:
    """
    현재 암호화폐 시장의 전반적인 상황을 브리핑합니다. 시장 심리, 자금 흐름, 주요 자금 이동(고래) 정보를 종합합니다.
    """
    fng_result, global_result, whale_result = await asyncio.gather(
        _fetch_fear_and_greed_index(),
        _fetch_global_market_data(),
        _fetch_whale_alerts(),
        return_exceptions=True
    )

    report = ["현재 시장 개요 브리핑:"]
    if (
        isinstance(fng_result, MarketSourceResult)
        and fng_result.status is MarketSourceStatus.OK
        and isinstance(fng_result.data, dict)
        and fng_result.data
    ):
        classification = _bounded_text(
            fng_result.data.get('value_classification'),
            MARKET_LABEL_MAX_CHARS,
        )
        value = _bounded_text(fng_result.data.get('value'), MARKET_LABEL_MAX_CHARS)
        report.append(f"- 시장 심리: '{classification}' (지수: {value})")
    else:
        report.append("- 시장 심리: Alternative.me 조회 실패로 확인 불가")

    if (
        isinstance(global_result, MarketSourceResult)
        and global_result.status is MarketSourceStatus.OK
        and isinstance(global_result.data, dict)
        and isinstance(global_result.data.get('market_cap_percentage'), dict)
    ):
        percentages = global_result.data['market_cap_percentage']
        btc_dom = _format_percentage(percentages.get('btc'))
        eth_dom = _format_percentage(percentages.get('eth'))
        report.append(f"- 시장 지배력: BTC {btc_dom}, ETH {eth_dom}")
    elif not (
        isinstance(global_result, MarketSourceResult)
        and global_result.status is MarketSourceStatus.NOT_CONFIGURED
    ):
        report.append("- 시장 지배력: CoinGecko 조회 실패로 확인 불가")

    if isinstance(whale_result, Exception):
        report.append("- 주요 자금 이동: Telegram 조회 실패로 확인 불가")
    elif isinstance(whale_result, WhaleAlertResult):
        if whale_result.status is TelegramFetchStatus.FETCH_FAILED:
            report.append("- 주요 자금 이동: Telegram 조회 실패로 확인 불가")
        elif whale_result.status is TelegramFetchStatus.UNAUTHORIZED:
            report.append("- 주요 자금 이동: Telegram 인증에 실패하여 확인 불가")
        elif whale_result.status is TelegramFetchStatus.UNAVAILABLE:
            report.append("- 주요 자금 이동: Telegram을 사용할 수 없어 확인 불가")
        elif whale_result.status is TelegramFetchStatus.NOT_CONFIGURED:
            report.append("- 주요 자금 이동: Telegram이 설정되지 않아 확인 불가")
        elif whale_result.status is TelegramFetchStatus.NO_MESSAGES:
            report.append("- 주요 자금 이동: 최근 1시간 내 포착된 움직임 없음")
        elif whale_result.status is TelegramFetchStatus.OK and whale_result.messages:
            report.append("- 주요 자금 이동 (지난 1시간):")
            for alert in whale_result.messages:
                report.append(f"  - {_format_whale_alert(alert)}")
        else:
            report.append("- 주요 자금 이동: 포착된 움직임 없음")
    else:
        report.append("- 주요 자금 이동: Telegram 조회 실패로 확인 불가")

    return "\n".join(report)

@mcp.tool()
async def get_coin_details(coin_id: CoinGeckoId | None = None) -> str:
    """
    특정 암호화폐의 상세 정보를 제공합니다. CoinGecko ID(예: 'bitcoin')를 입력해야 합니다.
    """
    if coin_id is None or not coin_id.strip():
        raise CryptoToolError(
            ToolErrorCode.COIN_ID_REQUIRED,
            "CoinGecko 코인 ID를 입력해주세요.",
        )
    coin_id = coin_id.strip()
    if len(coin_id) > COIN_ID_MAX_CHARS or not COIN_ID_PATTERN.fullmatch(coin_id):
        raise CryptoToolError(
            ToolErrorCode.COIN_ID_INVALID,
            "CoinGecko 코인 ID는 영문, 숫자, 밑줄, 하이픈으로 구성된 200자 이하의 값이어야 합니다.",
        )
    if not COINGECKO_API_KEY:
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_API_KEY_MISSING,
            "서버에 CoinGecko API 키(COINGECKO_API_KEY)가 설정되지 않았습니다.",
        )
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&community_data=false&developer_data=false"
        headers = {'x-cg-demo-api-key': COINGECKO_API_KEY}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            details = response.json()

        try:
            CoinDetailsIdentity.model_validate(details)
        except ValidationError:
            print(f"CoinGecko coin detail payload invalid for {coin_id}.")
            raise CryptoToolError(
                ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
                f"'{coin_id}' 정보 응답을 확인할 수 없습니다.",
            )

        return _format_coin_details(details)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise CryptoToolError(
                ToolErrorCode.COIN_NOT_FOUND,
                f"'{coin_id}' 코인을 찾을 수 없습니다. ID를 확인해주세요.",
            )
        print(f"CoinGecko coin detail HTTP error for {coin_id}: {e}")
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
            f"'{coin_id}' 정보 조회 중 CoinGecko API 오류가 발생했습니다.",
        )
    except CryptoToolError:
        raise
    except Exception as e:
        print(f"CoinGecko coin detail fetch error for {coin_id}: {e}")
        raise CryptoToolError(
            ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
            f"'{coin_id}' 정보를 가져오는 데 실패했습니다.",
        )


# FastMCP validates required arguments before tool code. Keep the public schema
# required while the runtime default lets omitted calls return the domain error.
get_coin_details.parameters["properties"]["coin_id"] = {
    "type": "string",
    **COIN_ID_JSON_SCHEMA,
}
get_coin_details.parameters["required"] = ["coin_id"]

@mcp.tool(output_schema=RealtimeNewsOutput.model_json_schema())
async def get_realtime_news(hours: NewsHours = 1) -> ToolResult:
    """
    주요 텔레그램 채널에서 최신 암호화폐 뉴스를 목록 형태로 가져옵니다.
    각 항목은 channel, message_id, timestamp, preview, truncated 정보를 포함합니다.
    원문 전체는 get_telegram_message 도구로 조회할 수 있습니다.
    최대 72시간(3일) 전까지의 뉴스만 조회가 가능합니다.

    Args:
        hours (int, optional): 현재로부터 몇 시간 전까지의 뉴스를 가져올지 지정합니다. 기본값은 1, 최대값은 72입니다.
    """
    if (
        not isinstance(hours, int)
        or isinstance(hours, bool)
        or not (1 <= hours <= 72)
    ):
        raise CryptoToolError(
            ToolErrorCode.NEWS_HOURS_INVALID,
            "'hours' 파라미터는 1과 72 사이의 값이어야 합니다.",
        )

    channels = ['wublockchainenglish', 'watcherguru']
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    client = await _get_telegram_client()

    tasks = [_fetch_news_channel(client, ch, since) for ch in channels]
    results = await asyncio.gather(*tasks)

    all_messages: list[NewsMessage] = []
    failed_channels: dict[str, NewsChannelFailureCode] = {}

    for result in results:
        all_messages.extend(result.messages)
        if result.failure is not None:
            failed_channels[result.channel] = result.failure

    all_messages.sort(key=lambda message: message.date, reverse=True)

    if not all_messages and set(failed_channels) == set(channels):
        if all(
            failure is NewsChannelFailureCode.TIMEOUT
            for failure in failed_channels.values()
        ):
            raise CryptoToolError(
                ToolErrorCode.TELEGRAM_TIMEOUT,
                "모든 Telegram 뉴스 채널 조회 시간이 초과되었습니다.",
            )
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_UPSTREAM_ERROR,
            "모든 Telegram 뉴스 채널을 조회하는 데 실패했습니다.",
        )

    if all_messages:
        report = [f"지난 {hours}시간 동안의 주요 뉴스:"]
        for message in all_messages:
            item = message.item
            trunc = " (원문 참조 필요)" if item.truncated else ""
            report.append(
                f"- [{item.timestamp}] @{item.channel} #{item.message_id} / "
                f"{item.preview}{trunc}"
            )
    elif failed_channels:
        report = [
            f"지난 {hours}시간 동안 조회에 성공한 채널에서는 새로운 뉴스가 없습니다."
        ]
    else:
        report = [f"지난 {hours}시간 동안 지정된 채널에서 새로운 뉴스가 없습니다."]

    if failed_channels:
        report.append("")
        report.append("조회 실패 채널:")
        for ch, failure in failed_channels.items():
            report.append(
                f"  @{ch}: 조회 실패: {NEWS_CHANNEL_FAILURE_MESSAGES[failure]}"
            )

    output = RealtimeNewsOutput(
        hours=hours,
        messages=[message.item for message in all_messages],
        failed_channels=[
            NewsChannelFailure(
                channel=channel,
                code=failure,
                message=NEWS_CHANNEL_FAILURE_MESSAGES[failure],
            )
            for channel, failure in failed_channels.items()
        ],
    )
    return ToolResult(
        content=[TextContent(type="text", text="\n".join(report))],
        structured_content=output.model_dump(mode="json"),
    )


@mcp.tool()
async def get_telegram_message(
    channel: TelegramChannel,
    message_id: TelegramMessageId,
) -> str:
    """
    텔레그램 채널의 특정 메시지 원문을 조회합니다.

    Args:
        channel (str): 채널 이름 (예: 'wublockchainenglish', 'watcherguru', 'whale_alert_io')
        message_id (int): 조회할 메시지 ID
    """
    if channel not in ALLOWED_TELEGRAM_CHANNELS:
        allowed = ", ".join(sorted(ALLOWED_TELEGRAM_CHANNELS))
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_CHANNEL_NOT_ALLOWED,
            f"허용되지 않은 채널입니다. 허용 채널: {allowed}",
        )
    if (
        not isinstance(message_id, int)
        or isinstance(message_id, bool)
        or message_id < 1
    ):
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_MESSAGE_ID_INVALID,
            "'message_id' 파라미터는 1 이상의 정수여야 합니다.",
        )

    client = await _get_telegram_client()

    try:
        async with asyncio.timeout(TELEGRAM_OPERATION_TIMEOUT_SECONDS):
            msg = await client.get_messages(channel, ids=message_id)
        if not msg:
            raise CryptoToolError(
                ToolErrorCode.TELEGRAM_MESSAGE_NOT_FOUND,
                f"채널 '{channel}'에서 메시지 ID {message_id}를 찾을 수 없습니다.",
            )
        if not msg.text:
            raise CryptoToolError(
                ToolErrorCode.TELEGRAM_MESSAGE_NOT_TEXT,
                f"메시지 ID {message_id}는 텍스트 콘텐츠를 포함하지 않습니다.",
            )
        return msg.text
    except TimeoutError:
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_TIMEOUT,
            "Telegram 메시지 조회 시간이 초과되었습니다.",
        )
    except CryptoToolError:
        raise
    except Exception as e:
        print(f"Telegram message fetch error for {channel}#{message_id}: {e}")
        raise CryptoToolError(
            ToolErrorCode.TELEGRAM_UPSTREAM_ERROR,
            "메시지 조회 중 Telegram 오류가 발생했습니다.",
        )


def run() -> int:
    print("🚀 Intelligent Crypto Assistant (FastMCP) 서버를 시작합니다.", flush=True)
    print("   - 서버 주소: http://0.0.0.0:8123", flush=True)
    print("   - 종료하려면 Ctrl+C를 누르세요.", flush=True)

    try:
        mcp.run(
            transport="streamable-http",
            host="0.0.0.0",
            port=8123,
            uvicorn_config={"timeout_graceful_shutdown": 5},
        )
    except KeyboardInterrupt:
        print("\n서버를 안전하게 종료했습니다.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
