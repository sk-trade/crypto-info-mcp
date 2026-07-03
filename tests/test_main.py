import importlib.util
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location("main_module", Path(__file__).resolve().parents[1] / "src" / "main.py")
main_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main_module)


def _tool_callable(name):
    return getattr(main_module, name).fn


@pytest.fixture(autouse=True)
def reset_runtime_state(monkeypatch):
    monkeypatch.setattr(main_module, "telegram_client", None)
    monkeypatch.setattr(main_module, "COINGECKO_API_KEY", None)
    monkeypatch.setattr(main_module, "TELEGRAM_API_ID", None)
    monkeypatch.setattr(main_module, "TELEGRAM_API_HASH", None)
    monkeypatch.setattr(main_module, "TELEGRAM_SESSION_STRING", None)
    yield
    monkeypatch.setattr(main_module, "telegram_client", None)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self.response


class FakeTelegramClient:
    def __init__(self, messages_by_channel):
        self.messages_by_channel = messages_by_channel

    async def iter_messages(self, channel, **kwargs):
        for message in self.messages_by_channel.get(channel, []):
            yield message

    def is_connected(self):
        return True


class FailingStartupTelegramClient:
    def __init__(self, *args, **kwargs):
        self.disconnected = False

    async def connect(self):
        raise RuntimeError("connection failed")

    def is_connected(self):
        return False

    async def disconnect(self):
        self.disconnected = True


class UnauthorizedStartupTelegramClient:
    def __init__(self, *args, **kwargs):
        self.disconnected = False

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return False

    def is_connected(self):
        return True

    async def disconnect(self):
        self.disconnected = True


class FakeMessage:
    def __init__(self, text, date):
        self.text = text
        self.date = date


@pytest.mark.asyncio
async def test_market_overview_combines_available_sources(monkeypatch):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({"value": "70", "value_classification": "Greed"}))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({"market_cap_percentage": {"btc": 52.3, "eth": 18.1}}))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved(["Whale moved 1,000 BTC"]))

    report = await _tool_callable("get_market_overview")()

    assert "Greed" in report
    assert "BTC 52.3%" in report
    assert "Whale moved 1,000 BTC" in report


@pytest.mark.asyncio
async def test_market_overview_reports_no_whale_movement_when_telegram_unavailable(monkeypatch):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({"value": "55", "value_classification": "Neutral"}))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({"market_cap_percentage": {"btc": 48.0, "eth": 16.0}}))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved([]))

    report = await _tool_callable("get_market_overview")()

    assert "포착된 움직임 없음" in report


@pytest.mark.asyncio
async def test_market_overview_reports_no_whale_movement_when_no_alerts(monkeypatch):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved([]))

    report = await _tool_callable("get_market_overview")()

    assert "포착된 움직임 없음" in report


@pytest.mark.asyncio
async def test_coin_details_requires_api_key():
    with pytest.raises(main_module.FastMCPError, match="CoinGecko API 키"):
        await _tool_callable("get_coin_details")("bitcoin")


@pytest.mark.asyncio
@pytest.mark.parametrize("coin_id", ["", "   "])
async def test_coin_details_rejects_missing_coin_id_without_request(monkeypatch, coin_id):
    main_module.COINGECKO_API_KEY = "test-key"

    class _FailingAsyncClient:
        def __init__(self):
            raise AssertionError("CoinGecko request should not be created")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: _FailingAsyncClient())

    with pytest.raises(main_module.FastMCPError, match="CoinGecko 코인 ID"):
        await _tool_callable("get_coin_details")(coin_id)


@pytest.mark.asyncio
async def test_coin_details_formats_successful_response(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    response = FakeResponse(
        {
            "name": "Bitcoin",
            "symbol": "btc",
            "market_cap_rank": 1,
            "market_data": {"current_price": {"krw": 123456789}},
            "links": {"homepage": ["https://bitcoin.org"]},
        }
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeAsyncClient(response))

    report = await _tool_callable("get_coin_details")("bitcoin")

    assert "Bitcoin" in report
    assert "BTC" in report
    assert "₩123,456,789" in report
    assert "https://bitcoin.org" in report


@pytest.mark.asyncio
async def test_coin_details_maps_missing_coin_to_clear_error(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    response = FakeResponse({}, status_code=404)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeAsyncClient(response))

    with pytest.raises(main_module.FastMCPError, match="코인을 찾을 수 없습니다"):
        await _tool_callable("get_coin_details")("missing-coin")


@pytest.mark.asyncio
async def test_realtime_news_rejects_invalid_hours():
    with pytest.raises(main_module.FastMCPError, match="1과 72 사이"):
        await _tool_callable("get_realtime_news")(0)


@pytest.mark.asyncio
async def test_telegram_client_helper_raises_when_disabled():
    with pytest.raises(main_module.FastMCPError, match="초기화되지 않았거나 인증에 실패"):
        await main_module._get_telegram_client()


@pytest.mark.asyncio
async def test_lifespan_disables_telegram_when_startup_connection_fails(monkeypatch):
    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", FailingStartupTelegramClient)

    async with main_module.lifespan(None):
        assert main_module.telegram_client is None


@pytest.mark.asyncio
async def test_lifespan_disconnects_telegram_when_startup_authorization_fails(monkeypatch):
    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    fake_client = UnauthorizedStartupTelegramClient()

    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: fake_client)

    async with main_module.lifespan(None):
        assert main_module.telegram_client is None

    assert fake_client.disconnected is True


@pytest.mark.asyncio
async def test_realtime_news_uses_telegram_client(monkeypatch):
    main_module.telegram_client = FakeTelegramClient(
        {
            "wublockchainenglish": [FakeMessage("line1\nline2", date=_dt())],
            "watcherguru": [],
        }
    )

    report = await _tool_callable("get_realtime_news")(1)

    assert "지난 1시간 동안의 주요 뉴스" in report
    assert "line1 line2" in report


@pytest.mark.asyncio
async def test_realtime_news_errors_when_telegram_disabled():
    with pytest.raises(main_module.FastMCPError, match="초기화되지 않았거나 인증에 실패"):
        await _tool_callable("get_realtime_news")(1)


def _resolved(value):
    async def _inner():
        return value

    return _inner()


def _dt():
    from datetime import datetime, timezone

    return datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc)
