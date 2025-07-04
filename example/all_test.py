import asyncio
import requests
import os
import json
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from datetime import datetime, timedelta, timezone
import time

# --- 0. 설정 및 초기화 ---
load_dotenv()

# API 키 및 설정 로드
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = 'telegram_session'
TARGET_CHANNELS = ['wublockchainenglish', 'watcherguru']

# --- 유틸리티 함수 ---

def print_header(title):
    """테스트 결과 출력을 위한 구분선을 포함한 헤더를 출력합니다."""
    print("\n" + "="*20 + f" {title} " + "="*20)

def print_json(data):
    """JSON(딕셔너리) 데이터를 사람이 읽기 쉽게 들여쓰기하여 출력합니다."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


# --- API 테스트 함수들 ---

def test_coingecko_api():
    """CoinGecko API를 테스트하여 특정 코인 정보를 가져옵니다."""
    print_header("1. CoinGecko API 테스트 (시장 데이터)")
    if not COINGECKO_API_KEY:
        print("오류: .env 파일에 COINGECKO_API_KEY가 설정되지 않았습니다.")
        return

    coin_id = "bitcoin"
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    headers = {'accept': 'application/json', 'x-cg-demo-api-key': COINGECKO_API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        print("✅ CoinGecko API 호출 성공!")

        market_data = data.get('market_data', {})
        current_price_krw = market_data.get('current_price', {}).get('krw')
        market_cap_rank = data.get('market_cap_rank')

        print(f"  - 코인 이름: {data.get('name')}")
        print(f"  - 현재 가격 (KRW): ₩{current_price_krw:,}")
        print(f"  - 시가총액 순위: {market_cap_rank}위")

    except requests.exceptions.RequestException as e:
        print(f"❌ CoinGecko API 호출 실패: {e}")

async def test_telegram_news_api(client: TelegramClient):
    """Telegram API를 테스트하여 지정된 채널에서 최신 뉴스를 가져옵니다."""
    print_header("2. Telegram API 테스트 (실시간 뉴스)")
    hours = 24  # 지난 24시간 동안의 뉴스 검색

    for channel in TARGET_CHANNELS:
        print(f"\n--- 채널: @{channel} | 지난 {hours}시간 뉴스 ---")
        time_offset = datetime.now(timezone.utc) - timedelta(hours=hours)
        messages_found = []

        try:
            # 지정된 채널에서 메시지를 5개까지 가져옵니다.
            async for message in client.iter_messages(channel, offset_date=time_offset, reverse=True, limit=5):
                if message.text:
                    messages_found.append(message)

            if not messages_found:
                print("  -> 해당 시간 범위 내에 뉴스가 없습니다.")
            else:
                print(f"✅ 채널 '{channel}' 뉴스 가져오기 성공! (최대 5개 표시)")
                for msg in messages_found:
                    time_str = msg.date.strftime('%Y-%m-%d %H:%M')
                    text_preview = msg.text.replace('\n', ' ')[:70] + "..."
                    print(f"  - [{time_str}] {text_preview}")

        except Exception as e:
            print(f"❌ 채널 '{channel}' 처리 중 오류: {e}")

        await asyncio.sleep(1) # API 요청 제한을 피하기 위한 채널 간 딜레이

def test_alternative_me_api():
    """Alternative.me API를 테스트하여 '공포 및 탐욕 지수'를 가져옵니다."""
    print_header("3. Alternative.me API 테스트 (공포/탐욕 지수)")
    url = "https://api.alternative.me/fng/?limit=1"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        print("✅ Alternative.me API 호출 성공!")

        index_data = data.get('data', [])[0]
        value = index_data.get('value')
        classification = index_data.get('value_classification')
        print(f"  - 현재 지수: {value} ({classification})")

    except requests.exceptions.RequestException as e:
        print(f"❌ Alternative.me API 호출 실패: {e}")


# --- 메인 실행 로직 ---

async def main():
    """모든 API 테스트를 순차적으로 실행하는 메인 비동기 함수입니다."""
    test_coingecko_api()
    time.sleep(1)
    test_alternative_me_api()

    async with TelegramClient(SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
        await test_telegram_news_api(client)

if __name__ == "__main__":
    # 처음 실행 시 텔레그램 인증(전화번호, 코드 입력)이 필요할 수 있습니다.
    print("### 최종 통합 테스트 시작 ###")
    asyncio.run(main())
    print("\n### 최종 통합 테스트 완료 ###")