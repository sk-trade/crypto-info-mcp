import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "generate_session_module",
    Path(__file__).resolve().parents[1] / "scripts" / "generate_session.py",
)
generate_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate_session)


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    monkeypatch.setattr(generate_session, "load_dotenv", lambda: None)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)


def test_load_telegram_config_requires_credentials():
    with pytest.raises(RuntimeError, match="TELEGRAM_API_ID, TELEGRAM_API_HASH"):
        generate_session.load_telegram_config()


def test_load_telegram_config_requires_integer_api_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "not-an-int")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")

    with pytest.raises(RuntimeError, match="must be an integer"):
        generate_session.load_telegram_config()


def test_load_telegram_config_reads_valid_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")

    assert generate_session.load_telegram_config() == (123456, "hash")


@pytest.mark.asyncio
async def test_main_starts_client_prints_session_and_disconnects(monkeypatch, capsys):
    created_clients = []

    class FakeSession:
        def save(self):
            return "session-string"

    class FakeTelegramClient:
        def __init__(self, session, api_id, api_hash):
            self.session = FakeSession()
            self.api_id = api_id
            self.api_hash = api_hash
            self.started = False
            self.disconnected = False
            created_clients.append(self)

        async def start(self):
            self.started = True

        async def is_user_authorized(self):
            return True

        def is_connected(self):
            return True

        async def disconnect(self):
            self.disconnected = True

    monkeypatch.setattr(generate_session, "load_telegram_config", lambda: (123456, "hash"))
    monkeypatch.setattr(generate_session, "StringSession", lambda: object())
    monkeypatch.setattr(generate_session, "TelegramClient", FakeTelegramClient)

    await generate_session.main()

    assert created_clients[0].api_id == 123456
    assert created_clients[0].api_hash == "hash"
    assert created_clients[0].started is True
    assert created_clients[0].disconnected is True
    assert "session-string" in capsys.readouterr().out
