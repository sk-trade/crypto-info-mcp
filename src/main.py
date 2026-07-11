import asyncio
from contextlib import asynccontextmanager
import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

from fastmcp import FastMCP
from fastmcp.exceptions import FastMCPError

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

telegram_client = None
WHALE_ALERT_MAX_CHARS = 300
MARKET_LABEL_MAX_CHARS = 100
COIN_NAME_MAX_CHARS = 120
COIN_SYMBOL_MAX_CHARS = 20
HOMEPAGE_MAX_CHARS = 500


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
    homepage_url = _bounded_text(homepage[0], HOMEPAGE_MAX_CHARS) if isinstance(homepage, list) and homepage else 'N/A'

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
            await client.disconnect()
            return True
    except Exception as e:
        print(f"텔레그램 연결 해제 중 오류가 발생했지만 무시합니다: {e}")
    return False


@asynccontextmanager
async def lifespan(app: FastMCP):
    """
    서버 시작 시 초기화된 전역 텔레그램 클라이언트 인스턴스를 반환합니다.
    """
    global telegram_client
    if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING]):
        print("텔레그램 환경 변수가 설정되지않아 관련 기능이 비활성화됩니다.")
    else:
        client = None
        try:
            print("Connecting to Telegram...")
            client = TelegramClient(StringSession(TELEGRAM_SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                print("텔레그램 인증이 필요합니다. 로컬에서 스크립트를 실행하여 세션 파일을 생성해주세요.")
                await _disconnect_telegram_client(client)
                telegram_client = None
            else:
                telegram_client = client
                print("텔레그램 클라이언트 연결 완료.")
        except Exception as e:
            print(f"텔레그램 초기화 실패로 관련 기능이 비활성화됩니다: {e}")
            telegram_client = None
            await _disconnect_telegram_client(client)
    try:
        yield
    finally:
        client = telegram_client
        telegram_client = None
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
        raise FastMCPError("텔레그램 클라이언트가 초기화되지 않았거나 인증에 실패했습니다.")
    return telegram_client

async def _fetch_fear_and_greed_index():
    """alternative.me에서 최신 공포 및 탐욕 지수를 비동기적으로 가져옵니다."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data') if isinstance(payload, dict) else None
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data[0]
            return {}
    except Exception as e:
        print(f"Fear & Greed Index Fetch Error: {e}")
        return {}

async def _fetch_global_market_data():
    """CoinGecko API에서 글로벌 마켓 데이터(예: 도미넌스)를 비동기적으로 가져옵니다."""
    if not COINGECKO_API_KEY: return {}
    try:
        url = "https://api.coingecko.com/api/v3/global"
        headers = {'x-cg-demo-api-key': COINGECKO_API_KEY}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data') if isinstance(payload, dict) else None
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Global Market Data Fetch Error: {e}")
        return {}


class TelegramStatus:
    NOT_CONFIGURED = "not_configured"
    AUTH_FAILED = "auth_failed"
    FETCH_FAILED = "fetch_failed"
    NO_MESSAGES = "no_messages"
    OK = "ok"


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


async def _fetch_whale_alerts():
    """텔레그램 'whale_alert_io' 채널에서 지난 1시간 동안의 메시지를 가져옵니다."""
    if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING]):
        return (TelegramStatus.NOT_CONFIGURED, "")

    try:
        client = await _get_telegram_client()
    except FastMCPError as e:
        return (TelegramStatus.AUTH_FAILED, str(e))

    messages_text = []
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        # Telethon's default iteration is newest first, so the first older post ends the window.
        async for message in client.iter_messages('whale_alert_io', limit=5):
            if _is_before_since(message, since):
                break
            if message.text:
                messages_text.append(message.text)
    except Exception as e:
        print(f"Whale Alert Fetch Error: {e}")
        return (TelegramStatus.FETCH_FAILED, str(e))
    if not messages_text:
        return (TelegramStatus.NO_MESSAGES, "")
    return (TelegramStatus.OK, messages_text)


# --- MCP 도구 함수 정의 ---

@mcp.tool()
async def get_market_overview() -> str:
    """
    현재 암호화폐 시장의 전반적인 상황을 브리핑합니다. 시장 심리, 자금 흐름, 주요 자금 이동(고래) 정보를 종합합니다.
    """
    fng_data, global_data, whale_result = await asyncio.gather(
        _fetch_fear_and_greed_index(),
        _fetch_global_market_data(),
        _fetch_whale_alerts(),
        return_exceptions=True
    )

    report = ["현재 시장 개요 브리핑:"]
    if isinstance(fng_data, dict) and fng_data:
        classification = _bounded_text(fng_data.get('value_classification'), MARKET_LABEL_MAX_CHARS)
        value = _bounded_text(fng_data.get('value'), MARKET_LABEL_MAX_CHARS)
        report.append(f"- 시장 심리: '{classification}' (지수: {value})")

    if isinstance(global_data, dict) and global_data and 'market_cap_percentage' in global_data:
        percentages = global_data['market_cap_percentage']
        if isinstance(percentages, dict):
            btc_dom = _format_percentage(percentages.get('btc'))
            eth_dom = _format_percentage(percentages.get('eth'))
            report.append(f"- 시장 지배력: BTC {btc_dom}, ETH {eth_dom}")

    if isinstance(whale_result, Exception):
        report.append("- 주요 자금 이동: Telegram 조회 실패로 확인 불가")
    elif isinstance(whale_result, list):
        if whale_result:
            report.append("- 주요 자금 이동 (지난 1시간):")
            for alert in whale_result:
                report.append(f"  - {_format_whale_alert(alert)}")
        else:
            report.append("- 주요 자금 이동: 포착된 움직임 없음")
    elif isinstance(whale_result, tuple) and len(whale_result) == 2:
        status, data = whale_result
        if status == TelegramStatus.FETCH_FAILED:
            report.append("- 주요 자금 이동: Telegram 조회 실패로 확인 불가")
        elif status == TelegramStatus.AUTH_FAILED:
            report.append("- 주요 자금 이동: Telegram 인증에 실패하여 확인 불가")
        elif status == TelegramStatus.NOT_CONFIGURED:
            report.append("- 주요 자금 이동: Telegram이 설정되지 않아 확인 불가")
        elif status == TelegramStatus.NO_MESSAGES:
            report.append("- 주요 자금 이동: 최근 1시간 내 포착된 움직임 없음")
        elif isinstance(data, list) and data:
            report.append("- 주요 자금 이동 (지난 1시간):")
            for alert in data:
                report.append(f"  - {_format_whale_alert(alert)}")
        else:
            report.append("- 주요 자금 이동: 포착된 움직임 없음")
    else:
        report.append("- 주요 자금 이동: 포착된 움직임 없음")

    return "\n".join(report)

@mcp.tool()
async def get_coin_details(coin_id: str) -> str:
    """
    특정 암호화폐의 상세 정보를 제공합니다. CoinGecko ID(예: 'bitcoin')를 입력해야 합니다.
    """
    if not coin_id or not coin_id.strip():
        raise FastMCPError("CoinGecko 코인 ID를 입력해주세요.")
    coin_id = coin_id.strip()
    if not COINGECKO_API_KEY:
        raise FastMCPError("서버에 CoinGecko API 키가 설정되지 않았습니다.")
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&community_data=false&developer_data=false"
        headers = {'x-cg-demo-api-key': COINGECKO_API_KEY}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            details = response.json()

        return _format_coin_details(details)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise FastMCPError(f"'{coin_id}' 코인을 찾을 수 없습니다. ID를 확인해주세요.")
        print(f"CoinGecko coin detail HTTP error for {coin_id}: {e}")
        raise FastMCPError(f"'{coin_id}' 정보 조회 중 CoinGecko API 오류가 발생했습니다.")
    except Exception as e:
        print(f"CoinGecko coin detail fetch error for {coin_id}: {e}")
        raise FastMCPError(f"'{coin_id}' 정보를 가져오는 데 실패했습니다.")

@mcp.tool()
async def get_realtime_news(hours: int = 1) -> str:
    """
    주요 텔레그램 채널에서 최신 암호화폐 뉴스를 목록 형태로 가져옵니다.
    각 항목은 channel, message_id, timestamp, preview, truncated 정보를 포함합니다.
    원문 전체는 get_telegram_message 도구로 조회할 수 있습니다.
    최대 72시간(3일) 전까지의 뉴스만 조회가 가능합니다.

    Args:
        hours (int, optional): 현재로부터 몇 시간 전까지의 뉴스를 가져올지 지정합니다. 기본값은 1, 최대값은 72입니다.
    """
    if not (1 <= hours <= 72):
        raise FastMCPError("'hours' 파라미터는 1과 72 사이의 값이어야 합니다.")

    channels = ['wublockchainenglish', 'watcherguru']
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    client = await _get_telegram_client()

    async def fetch_for_channel(ch):
        messages = []
        errors = []
        try:
            # Request newest posts first and stop at the first one outside the time window.
            async for msg in client.iter_messages(ch, limit=10):
                if _is_before_since(msg, since):
                    break
                if msg.text:
                    message_date = _as_utc(msg.date) if getattr(msg, "date", None) else since
                    preview = msg.text.replace('\n', ' ').strip()
                    if len(preview) > 150:
                        preview = preview[:147] + "..."
                        truncated = True
                    else:
                        truncated = False
                    messages.append({
                        "channel": ch,
                        "message_id": getattr(msg, "id", None),
                        "date": message_date,
                        "timestamp": message_date.strftime('%m-%d %H:%M UTC'),
                        "preview": preview,
                        "truncated": truncated,
                    })
        except Exception as e:
            print(f"Telegram news fetch error for {ch}: {e}")
            errors.append("채널을 조회할 수 없습니다.")
        return messages, errors

    tasks = [fetch_for_channel(ch) for ch in channels]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_messages = []
    channel_statuses = {}

    for i, result in enumerate(results):
        ch = channels[i]
        if isinstance(result, Exception):
            print(f"Telegram news task error for {ch}: {result}")
            channel_statuses[ch] = "조회 실패: 채널을 조회할 수 없습니다."
        elif isinstance(result, tuple) and len(result) == 2:
            msgs, errors = result
            all_messages.extend(msgs)
            if errors:
                channel_statuses[ch] = f"조회 실패: {errors[0]}"
            else:
                channel_statuses[ch] = f"{len(msgs)}건 조회됨"
        else:
            channel_statuses[ch] = "알 수 없는 오류"

    all_messages.sort(key=lambda m: m["date"], reverse=True)
    failed_channels = {ch: status for ch, status in channel_statuses.items() if "실패" in status}

    if not all_messages and not failed_channels:
        return f"지난 {hours}시간 동안 지정된 채널에서 새로운 뉴스가 없습니다."

    report = [f"지난 {hours}시간 동안의 주요 뉴스:"]
    for msg in all_messages:
        trunc = " (원문 참조 필요)" if msg["truncated"] else ""
        message_id = f" #{msg['message_id']}" if msg["message_id"] is not None else ""
        report.append(
            f"- [{msg['timestamp']}] @{msg['channel']}{message_id} / "
            f"{msg['preview']}{trunc}"
        )

    if failed_channels:
        report.append("")
        report.append("조회 실패 채널:")
        for ch, status in failed_channels.items():
            report.append(f"  @{ch}: {status}")

    return "\n".join(report)


@mcp.tool()
async def get_telegram_message(channel: str, message_id: int) -> str:
    """
    텔레그램 채널의 특정 메시지 원문을 조회합니다.

    Args:
        channel (str): 채널 이름 (예: 'wublockchainenglish', 'watcherguru', 'whale_alert_io')
        message_id (int): 조회할 메시지 ID
    """
    if channel not in ALLOWED_TELEGRAM_CHANNELS:
        allowed = ", ".join(sorted(ALLOWED_TELEGRAM_CHANNELS))
        raise FastMCPError(f"허용되지 않은 채널입니다. 허용 채널: {allowed}")
    if message_id < 1:
        raise FastMCPError("'message_id' 파라미터는 1 이상의 정수여야 합니다.")

    client = await _get_telegram_client()

    try:
        msg = await client.get_messages(channel, ids=message_id)
        if not msg:
            raise FastMCPError(f"채널 '{channel}'에서 메시지 ID {message_id}를 찾을 수 없습니다.")
        if not msg.text:
            raise FastMCPError(f"메시지 ID {message_id}는 텍스트 콘텐츠를 포함하지 않습니다.")
        return msg.text
    except FastMCPError:
        raise
    except Exception as e:
        print(f"Telegram message fetch error for {channel}#{message_id}: {e}")
        raise FastMCPError("메시지 조회 중 Telegram 오류가 발생했습니다.")


if __name__ == "__main__":
    print("🚀 Intelligent Crypto Assistant (FastMCP) 서버를 시작합니다.")
    print("   - 서버 주소: http://0.0.0.0:8123")
    print("   - 종료하려면 Ctrl+C를 누르세요.")

    mcp.run(transport="streamable-http", host="0.0.0.0", port=8123)
