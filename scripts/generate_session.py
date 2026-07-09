import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession


def load_telegram_config() -> tuple[int, str]:
    load_dotenv()
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    missing = [
        name
        for name, value in {
            "TELEGRAM_API_ID": api_id,
            "TELEGRAM_API_HASH": api_hash,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")

    try:
        parsed_api_id = int(api_id)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be an integer.") from exc

    return parsed_api_id, api_hash


async def main():
    api_id, api_hash = load_telegram_config()
    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        print("\nStarting Telegram login flow...")
        try:
            await client.start()
        except EOFError as exc:
            raise RuntimeError(
                "Telegram login requires interactive input. Re-run this command "
                "in an interactive terminal so Telethon can prompt for your phone, "
                "login code, or bot token."
            ) from exc
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram authorization failed; no session string was generated.")

        session_string = client.session.save()
        if not session_string:
            raise RuntimeError("Telegram returned an empty session string.")

        print("\nTELEGRAM_SESSION_STRING:")
        print(session_string)
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception as exc:
            print(f"Telegram cleanup failed: {exc}", file=sys.stderr)


def run() -> int:
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"Telegram session generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
