import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx  
from dotenv import load_dotenv
from telethon import TelegramClient

from fastmcp import FastMCP
from fastmcp.exceptions import FastMCPError

# --- 설정 및 전역 변수 초기화 ---
load_dotenv()

# API 키 및 설정 로드
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = 'telegram_session'

# FastMCP 앱 인스턴스 생성
mcp = FastMCP("Intelligent Crypto Assistant")

# 텔레그램 클라이언트 
telegram_client = None


# --- 내부 헬퍼 함수 ---

async def _get_telegram_client():
    """
    애플리케이션 전역에서 사용될 단일 텔레그램 클라이언트 인스턴스를 생성하고 연결합니다.
    이미 연결된 클라이언트가 있으면 그것을 반환합니다.
    """
    global telegram_client
    if telegram_client is None or not telegram_client.is_connected():
        client = TelegramClient(SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            print("텔레그램 인증이 필요합니다. 로컬에서 스크립트를 실행하여 세션 파일을 생성해주세요.")
        telegram_client = client
    return telegram_client

async def _fetch_fear_and_greed_index():
    """alternative.me에서 최신 공포 및 탐욕 지수를 비동기적으로 가져옵니다."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            response.raise_for_status()
            return response.json().get('data', [{}])[0]
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
            return response.json().get('data', {})
    except Exception as e:
        print(f"Global Market Data Fetch Error: {e}")
        return {}

async def _fetch_whale_alerts():
    """텔레그램 'whale_alert_io' 채널에서 지난 1시간 동안의 메시지를 가져옵니다."""
    client = await _get_telegram_client()
    messages_text = []
    try:
        time_offset = datetime.now(timezone.utc) - timedelta(hours=1)
        async for message in client.iter_messages('whale_alert_io', offset_date=time_offset, limit=5):
            if message.text:
                messages_text.append(message.text)
    except Exception as e:
        print(f"Whale Alert Fetch Error: {e}")
    return messages_text


# --- MCP 도구 함수 정의 ---

@mcp.tool()
async def get_market_overview() -> str:
    """
    현재 암호화폐 시장의 전반적인 상황을 브리핑합니다. 시장 심리, 자금 흐름, 주요 자금 이동(고래) 정보를 종합합니다.
    """
    # 여러 비동기 함수를 병렬로 실행하여 응답 시간 단축
    fng_data, global_data, whale_alerts = await asyncio.gather(
        _fetch_fear_and_greed_index(),
        _fetch_global_market_data(),
        _fetch_whale_alerts()
    )

    report = ["현재 시장 개요 브리핑:"]
    if fng_data:
        report.append(f"- 시장 심리: '{fng_data.get('value_classification', 'N/A')}' (지수: {fng_data.get('value', 'N/A')})")

    if global_data and 'market_cap_percentage' in global_data:
        btc_dom = global_data['market_cap_percentage'].get('btc', 0)
        eth_dom = global_data['market_cap_percentage'].get('eth', 0)
        report.append(f"- 시장 지배력: BTC {btc_dom:.1f}%, ETH {eth_dom:.1f}%")

    if whale_alerts:
        report.append("- 주요 자금 이동 (지난 1시간):")
        for alert in whale_alerts:
            cleaned_alert = alert.replace('\n', ' ').strip()
            report.append(f"  - {cleaned_alert}")
    else:
        report.append("- 주요 자금 이동 (지난 1시간): 포착된 움직임 없음")

    return "\n".join(report)

@mcp.tool()
async def get_coin_details(coin_id: str) -> str:
    """
    특정 암호화폐의 상세 정보를 제공합니다. CoinGecko ID(예: 'bitcoin')를 입력해야 합니다.
    """
    if not COINGECKO_API_KEY:
        raise FastMCPError("서버에 CoinGecko API 키가 설정되지 않았습니다.")
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&community_data=false&developer_data=false"
        headers = {'x-cg-demo-api-key': COINGECKO_API_KEY}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            details = response.json()

        price_krw = details.get('market_data', {}).get('current_price', {}).get('krw', 'N/A')

        report = [
            f"'{details.get('name', 'N/A')}' ({details.get('symbol', 'N/A').upper()}) 상세 정보:",
            f"- 시가총액 순위: {details.get('market_cap_rank', 'N/A')}위",
            f"- 현재 가격: ₩{price_krw:,}" if isinstance(price_krw, (int, float)) else f"현재 가격: {price_krw}",
            f"- 홈페이지: {details.get('links', {}).get('homepage', ['N/A'])[0]}"
        ]
        return "\n".join(report)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise FastMCPError(f"'{coin_id}' 코인을 찾을 수 없습니다. ID를 확인해주세요.")
        raise FastMCPError(f"'{coin_id}' 정보 조회 중 API 에러 발생: {e}")
    except Exception as e:
        raise FastMCPError(f"'{coin_id}' 정보를 가져오는 데 실패했습니다: {e}")

@mcp.tool()
async def get_realtime_news(hours: int = 1) -> str:
    """
    주요 텔레그램 채널에서 최신 암호화폐 뉴스를 가져옵니다. 최대 72시간(3일) 전까지의 뉴스만 조회가 가능합니다.

    Args:
        hours (int, optional): 현재로부터 몇 시간 전까지의 뉴스를 가져올지 지정합니다. 기본값은 1, 최대값은 72입니다.
    """
    if not (1 <= hours <= 72):
        raise FastMCPError("'hours' 파라미터는 1과 72 사이의 값이어야 합니다.")

    client = await _get_telegram_client()
    channels = ['wublockchainenglish', 'watcherguru']
    time_offset = datetime.now(timezone.utc) - timedelta(hours=hours)

    # 각 채널에서 메시지를 가져오는 작업을 비동기 태스크로 생성
    async def fetch_for_channel(ch):
        messages = []
        async for msg in client.iter_messages(ch, offset_date=time_offset, reverse=True, limit=10):
            if msg.text:
                cleaned_text = msg.text.replace('\n', ' ').strip()
                messages.append(f"- [{msg.date.strftime('%m-%d %H:%M')}] @{ch}: {cleaned_text}")
        return messages

    tasks = [fetch_for_channel(channel) for channel in channels]
    results = await asyncio.gather(*tasks)
    all_messages = [msg for sublist in results for msg in sublist]

    if not all_messages:
        return f"지난 {hours}시간 동안 지정된 채널에서 새로운 뉴스가 없습니다."

    report = [f"지난 {hours}시간 동안의 주요 뉴스:"] + all_messages
    return "\n".join(report)


if __name__ == "__main__":
    print("🚀 Intelligent Crypto Assistant (FastMCP) 서버를 시작합니다.")
    print("   - 서버 주소: http://0.0.0.0:8123")
    print("   - 종료하려면 Ctrl+C를 누르세요.")
    print("\n[필수] 텔레그램 기능을 사용하려면 'telegram_session.session' 파일이 필요합니다.")
    print("       파일이 없다면, 먼저 로컬 환경에서 이 스크립트를 실행하여 전화번호 인증을 완료하세요.\n")

    mcp.run(transport="streamable-http", host="0.0.0.0", port=8123)