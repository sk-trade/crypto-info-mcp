import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 1234
API_HASH = 'API_HASH'

async def main():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        print("\n 세션 생성중...")
        print(client.session.save())
        
if __name__ == '__main__':
    asyncio.run(main())

        