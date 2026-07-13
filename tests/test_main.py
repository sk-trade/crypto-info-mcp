import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

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


class RecordingTelegramClient(FakeTelegramClient):
    def __init__(self, messages_by_channel):
        super().__init__(messages_by_channel)
        self.calls = []

    async def iter_messages(self, channel, **kwargs):
        self.calls.append((channel, kwargs))
        async for message in super().iter_messages(channel, **kwargs):
            yield message


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


class UnauthorizedStartupDisconnectingTelegramClient:
    def __init__(self, *args, **kwargs):
        self.disconnect_calls = 0

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return False

    def is_connected(self):
        return True

    async def disconnect(self):
        self.disconnect_calls += 1
        raise RuntimeError("disconnect failed")


class AuthorizedStartupTelegramClient:
    def __init__(self, disconnect_fails=False):
        self.disconnect_fails = disconnect_fails
        self.disconnected = False

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    def is_connected(self):
        return True

    async def disconnect(self):
        self.disconnected = True
        if self.disconnect_fails:
            raise RuntimeError("disconnect failed")


class FakeMessage:
    def __init__(self, text, date, message_id=1):
        self.text = text
        self.date = date
        self.id = message_id


class AuthorizedMCPTestTelegramClient(AuthorizedStartupTelegramClient):
    async def iter_messages(self, channel, **kwargs):
        if channel == "wublockchainenglish":
            yield FakeMessage("protocol news preview", _dt(), message_id=42)

    async def get_messages(self, channel, ids):
        if channel == "wublockchainenglish" and ids == 42:
            return FakeMessage("protocol full message", _dt(), message_id=42)
        return None


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
async def test_market_overview_treats_unconfigured_telegram_as_no_whale_movement(monkeypatch):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({"value": "55", "value_classification": "Neutral"}))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({"market_cap_percentage": {"btc": 48.0, "eth": 16.0}}))

    report = await _tool_callable("get_market_overview")()

    assert "Telegram이 설정되지 않아 확인 불가" in report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (main_module.TelegramStatus.AUTH_FAILED, "Telegram 인증에 실패하여 확인 불가"),
        (main_module.TelegramStatus.FETCH_FAILED, "Telegram 조회 실패로 확인 불가"),
        (main_module.TelegramStatus.NO_MESSAGES, "최근 1시간 내 포착된 움직임 없음"),
    ],
)
async def test_market_overview_distinguishes_telegram_availability_states(monkeypatch, status, expected):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved((status, "failure detail")))

    report = await _tool_callable("get_market_overview")()

    assert expected in report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("percentages", "expected"),
    [
        ({"btc": None, "eth": "unknown"}, "BTC N/A, ETH N/A"),
        (None, None),
    ],
)
async def test_market_overview_tolerates_incomplete_dominance_payload(monkeypatch, percentages, expected):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({}))
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved({"market_cap_percentage": percentages}),
    )
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved([]))

    report = await _tool_callable("get_market_overview")()

    if expected is None:
        assert "시장 지배력" not in report
    else:
        assert expected in report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fng_data", "global_data"),
    [
        ("unexpected", {}),
        ({}, "market_cap_percentage"),
        ({}, ["market_cap_percentage"]),
    ],
)
async def test_market_overview_ignores_malformed_helper_results(monkeypatch, fng_data, global_data):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved(fng_data))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved(global_data))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved([]))

    report = await _tool_callable("get_market_overview")()

    assert report.startswith("현재 시장 개요 브리핑:")
    assert "포착된 움직임 없음" in report


@pytest.mark.asyncio
async def test_fetch_helpers_reject_malformed_data_shapes(monkeypatch):
    responses = iter(
        [
            FakeResponse({"data": "unexpected"}),
            FakeResponse({"data": ["unexpected"]}),
            FakeResponse({"data": "market_cap_percentage"}),
        ]
    )
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda: FakeAsyncClient(next(responses)),
    )
    main_module.COINGECKO_API_KEY = "test-key"

    assert await main_module._fetch_fear_and_greed_index() == {}
    assert await main_module._fetch_fear_and_greed_index() == {}
    assert await main_module._fetch_global_market_data() == {}


@pytest.mark.asyncio
async def test_market_overview_reports_no_whale_movement_when_no_alerts(monkeypatch):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved([]))

    report = await _tool_callable("get_market_overview")()

    assert "포착된 움직임 없음" in report


@pytest.mark.asyncio
async def test_market_overview_bounds_whale_alert_text(monkeypatch):
    monkeypatch.setattr(main_module, "_fetch_fear_and_greed_index", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved(["x" * 100_000]))

    report = await _tool_callable("get_market_overview")()

    alert_line = report.splitlines()[-1]
    assert alert_line.endswith("...")
    assert len(alert_line.removeprefix("  - ")) == main_module.WHALE_ALERT_MAX_CHARS
    assert len(report) < 1_000


@pytest.mark.asyncio
async def test_market_overview_bounds_fear_and_greed_fields(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved({"value": "1" * 100_000, "value_classification": "x" * 100_000}),
    )
    monkeypatch.setattr(main_module, "_fetch_global_market_data", lambda: _resolved({}))
    monkeypatch.setattr(main_module, "_fetch_whale_alerts", lambda: _resolved([]))

    report = await _tool_callable("get_market_overview")()

    assert report.count("...") == 2
    assert len(report) < 1_000


@pytest.mark.asyncio
async def test_coin_details_requires_api_key():
    with pytest.raises(main_module.FastMCPError, match="CoinGecko API 키"):
        await _tool_callable("get_coin_details")("bitcoin")


@pytest.mark.asyncio
@pytest.mark.parametrize("coin_id", ["", "   "])
async def test_coin_details_rejects_missing_coin_id_without_request(monkeypatch, coin_id):
    class _FailingAsyncClient:
        def __init__(self):
            raise AssertionError("CoinGecko request should not be created")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: _FailingAsyncClient())

    with pytest.raises(main_module.FastMCPError, match="CoinGecko 코인 ID"):
        await _tool_callable("get_coin_details")(coin_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("coin_id", ["x" * 201, "bitcoin/market", "bitcoin?x=1", "-bitcoin", "bitcoin-"])
async def test_coin_details_rejects_unsafe_or_oversized_coin_id_without_request(monkeypatch, coin_id):
    class _FailingAsyncClient:
        def __init__(self):
            raise AssertionError("CoinGecko request should not be created")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: _FailingAsyncClient())

    with pytest.raises(main_module.FastMCPError, match="200자 이하") as error:
        await _tool_callable("get_coin_details")(coin_id)

    assert coin_id not in str(error.value)


@pytest.mark.asyncio
async def test_coin_details_strips_coin_id_before_request(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    requested_urls = []

    class RecordingAsyncClient(FakeAsyncClient):
        async def get(self, url, **kwargs):
            requested_urls.append(url)
            return await super().get(url, **kwargs)

    response = FakeResponse({"name": "Bitcoin", "symbol": "btc"})
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: RecordingAsyncClient(response))

    await _tool_callable("get_coin_details")("  bitcoin  ")

    assert requested_urls == [
        "https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=false&developer_data=false"
    ]


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
async def test_coin_details_formats_null_and_empty_optional_fields(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    response = FakeResponse(
        {
            "name": "Unknown coin",
            "symbol": None,
            "market_cap_rank": None,
            "market_data": {"current_price": {"krw": None}},
            "links": {"homepage": []},
        }
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeAsyncClient(response))

    report = await _tool_callable("get_coin_details")("unknown")

    assert "Unknown coin" in report
    assert "(N/A)" in report
    assert "시가총액 순위: N/A위" in report
    assert "현재 가격: N/A" in report
    assert "홈페이지: N/A" in report


@pytest.mark.asyncio
async def test_coin_details_formats_null_links(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    response = FakeResponse({"name": "Unknown coin", "links": None, "market_data": None})
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeAsyncClient(response))

    report = await _tool_callable("get_coin_details")("unknown")

    assert "홈페이지: N/A" in report


@pytest.mark.asyncio
async def test_coin_details_bounds_and_validates_upstream_fields(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    response = FakeResponse(
        {
            "name": "n" * 100_000,
            "symbol": "s" * 100_000,
            "market_cap_rank": "first",
            "market_data": {"current_price": {"krw": "1" * 100_000}},
            "links": {"homepage": ["https://example.com/" + "x" * 100_000]},
        }
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeAsyncClient(response))

    report = await _tool_callable("get_coin_details")("bounded")

    assert "시가총액 순위: N/A위" in report
    assert "현재 가격: N/A" in report
    assert report.count("...") == 3
    assert len(report) < 1_000


@pytest.mark.asyncio
async def test_coin_details_maps_missing_coin_to_clear_error(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    response = FakeResponse({}, status_code=404)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeAsyncClient(response))

    with pytest.raises(main_module.FastMCPError, match="코인을 찾을 수 없습니다"):
        await _tool_callable("get_coin_details")("missing-coin")


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [0, 73, "1", True, 1.0, None])
async def test_realtime_news_rejects_invalid_hours(hours):
    with pytest.raises(main_module.FastMCPError, match="1과 72 사이"):
        await _tool_callable("get_realtime_news")(hours)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "field"),
    [
        ("get_realtime_news", {"hours": "1"}, "hours"),
        ("get_realtime_news", {"hours": True}, "hours"),
        ("get_realtime_news", {"hours": 1.0}, "hours"),
        (
            "get_telegram_message",
            {"channel": "watcherguru", "message_id": "1"},
            "message_id",
        ),
        (
            "get_telegram_message",
            {"channel": "watcherguru", "message_id": True},
            "message_id",
        ),
        (
            "get_telegram_message",
            {"channel": "watcherguru", "message_id": 1.0},
            "message_id",
        ),
    ],
)
async def test_mcp_integer_parameters_reject_coercible_non_integers(
    tool_name,
    arguments,
    field,
):
    async with Client(main_module.mcp) as client:
        result = await client.call_tool(tool_name, arguments, raise_on_error=False)

    error_text = "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )
    assert result.is_error is True
    assert field in error_text
    assert "valid integer" in error_text


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
async def test_lifespan_swallows_disconnect_failure_when_startup_authorization_fails(monkeypatch):
    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    fake_client = UnauthorizedStartupDisconnectingTelegramClient()

    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: fake_client)

    async with main_module.lifespan(None):
        assert main_module.telegram_client is None

    assert fake_client.disconnect_calls == 1


@pytest.mark.asyncio
async def test_lifespan_disconnects_authorized_client_and_clears_reference(monkeypatch):
    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    fake_client = AuthorizedStartupTelegramClient()
    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: fake_client)

    async with main_module.lifespan(None):
        assert main_module.telegram_client is fake_client

    assert fake_client.disconnected is True
    assert main_module.telegram_client is None


@pytest.mark.asyncio
async def test_lifespan_clears_reference_when_authorized_cleanup_fails(monkeypatch):
    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    fake_client = AuthorizedStartupTelegramClient(disconnect_fails=True)
    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: fake_client)

    async with main_module.lifespan(None):
        assert main_module.telegram_client is fake_client

    assert fake_client.disconnected is True
    assert main_module.telegram_client is None


@pytest.mark.asyncio
async def test_fastmcp_client_follows_news_reference_to_full_message(monkeypatch):
    main_module.TELEGRAM_API_ID = 123
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    fake_client = AuthorizedMCPTestTelegramClient()
    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: fake_client)

    async with Client(main_module.mcp) as client:
        news_result = await client.call_tool(
            "get_realtime_news",
            {"hours": 1},
            raise_on_error=False,
        )
        message_result = await client.call_tool(
            "get_telegram_message",
            {"channel": "wublockchainenglish", "message_id": 42},
            raise_on_error=False,
        )

    news_text = "\n".join(
        block.text for block in news_result.content if getattr(block, "text", None)
    )
    message_text = "\n".join(
        block.text for block in message_result.content if getattr(block, "text", None)
    )
    assert news_result.is_error is False
    assert "@wublockchainenglish #42 / protocol news preview" in news_text
    assert message_result.is_error is False
    assert message_text == "protocol full message"
    assert fake_client.disconnected is True
    assert main_module.telegram_client is None


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
    assert "@wublockchainenglish #1" in report
    assert "line1 line2" in report


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", [None, 0, True, "1"])
async def test_realtime_news_rejects_unusable_message_references(message_id):
    main_module.telegram_client = FakeTelegramClient(
        {
            "wublockchainenglish": [
                FakeMessage("unusable reference", _dt(), message_id=message_id),
            ],
            "watcherguru": [],
        }
    )

    report = await _tool_callable("get_realtime_news")(1)

    assert "unusable reference" not in report
    assert "@wublockchainenglish: 조회 실패: 메시지 참조를 확인할 수 없습니다." in report


@pytest.mark.asyncio
async def test_realtime_news_returns_newest_messages_and_stops_before_since():
    now = datetime.now(timezone.utc)
    client = RecordingTelegramClient(
        {
            "wublockchainenglish": [
                FakeMessage("newest", now - timedelta(minutes=5)),
                FakeMessage("older than window", now - timedelta(hours=2)),
            ],
            "watcherguru": [
                FakeMessage("also recent", now - timedelta(minutes=10)),
                FakeMessage("too old", now - timedelta(hours=3)),
            ],
        }
    )
    main_module.telegram_client = client

    report = await _tool_callable("get_realtime_news")(1)

    assert "newest" in report
    assert "also recent" in report
    assert "older than window" not in report
    assert "too old" not in report
    assert report.index("newest") < report.index("also recent")
    assert client.calls == [
        ("wublockchainenglish", {"limit": 10}),
        ("watcherguru", {"limit": 10}),
    ]


@pytest.mark.asyncio
async def test_realtime_news_normalizes_naive_dates_as_utc():
    naive_date = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    main_module.telegram_client = FakeTelegramClient(
        {
            "wublockchainenglish": [FakeMessage("naive timestamp", naive_date)],
            "watcherguru": [],
        }
    )

    report = await _tool_callable("get_realtime_news")(1)

    assert naive_date.strftime("%m-%d %H:%M UTC") in report
    assert "naive timestamp" in report


@pytest.mark.asyncio
async def test_realtime_news_preserves_results_when_one_channel_fails():
    class PartiallyFailingTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            if channel == "watcherguru":
                raise RuntimeError("channel unavailable")
            yield FakeMessage("available news", _dt())

    main_module.telegram_client = PartiallyFailingTelegramClient()

    report = await _tool_callable("get_realtime_news")(1)

    assert "available news" in report
    assert "조회 실패 채널" in report
    assert "@watcherguru: 조회 실패: 채널을 조회할 수 없습니다." in report
    assert "channel unavailable" not in report


@pytest.mark.asyncio
async def test_realtime_news_does_not_expose_unbounded_channel_errors():
    class FailingTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            raise RuntimeError("e" * 100_000)
            yield

    main_module.telegram_client = FailingTelegramClient()

    report = await _tool_callable("get_realtime_news")(1)

    assert report.count("채널을 조회할 수 없습니다.") == 2
    assert "e" * 1_000 not in report
    assert len(report) < 1_000


@pytest.mark.asyncio
async def test_whale_alerts_return_newest_messages_and_stop_before_since(monkeypatch):
    now = datetime.now(timezone.utc)
    client = RecordingTelegramClient(
        {
            "whale_alert_io": [
                FakeMessage("recent whale", now - timedelta(minutes=5)),
                FakeMessage("expired whale", now - timedelta(hours=2)),
            ]
        }
    )
    monkeypatch.setattr(main_module, "TELEGRAM_API_ID", 1)
    monkeypatch.setattr(main_module, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(main_module, "TELEGRAM_SESSION_STRING", "session")
    main_module.telegram_client = client

    status, alerts = await main_module._fetch_whale_alerts()

    assert status == main_module.TelegramStatus.OK
    assert alerts == ["recent whale"]
    assert client.calls == [("whale_alert_io", {"limit": 5})]


@pytest.mark.asyncio
async def test_realtime_news_reports_clear_no_news_when_channels_are_empty():
    main_module.telegram_client = FakeTelegramClient(
        {
            "wublockchainenglish": [],
            "watcherguru": [],
        }
    )

    report = await _tool_callable("get_realtime_news")(1)

    assert report == "지난 1시간 동안 지정된 채널에서 새로운 뉴스가 없습니다."


@pytest.mark.asyncio
async def test_realtime_news_errors_when_telegram_disabled():
    with pytest.raises(main_module.FastMCPError, match="초기화되지 않았거나 인증에 실패"):
        await _tool_callable("get_realtime_news")(1)


@pytest.mark.asyncio
async def test_telegram_message_rejects_disallowed_channel_before_client_lookup():
    with pytest.raises(main_module.FastMCPError, match="허용되지 않은 채널"):
        await _tool_callable("get_telegram_message")("not-allowed", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", [0, -1, "1", True, 1.0, None])
async def test_telegram_message_rejects_invalid_message_id_before_client_lookup(message_id):
    with pytest.raises(main_module.FastMCPError, match="1 이상의 정수"):
        await _tool_callable("get_telegram_message")("watcherguru", message_id)


@pytest.mark.asyncio
async def test_telegram_message_returns_full_text():
    class FakeMessageClient:
        async def get_messages(self, channel, ids):
            assert channel == "watcherguru"
            assert ids == 42
            return FakeMessage("full message", _dt())

    main_module.telegram_client = FakeMessageClient()

    result = await _tool_callable("get_telegram_message")("watcherguru", 42)

    assert result == "full message"


@pytest.mark.asyncio
async def test_telegram_message_reports_missing_and_non_text_messages():
    class FakeMessageClient:
        def __init__(self, message):
            self.message = message

        async def get_messages(self, channel, ids):
            return self.message

    main_module.telegram_client = FakeMessageClient(None)
    with pytest.raises(main_module.FastMCPError, match="찾을 수 없습니다"):
        await _tool_callable("get_telegram_message")("watcherguru", 42)

    main_module.telegram_client = FakeMessageClient(FakeMessage(None, _dt()))
    with pytest.raises(main_module.FastMCPError, match="텍스트 콘텐츠"):
        await _tool_callable("get_telegram_message")("watcherguru", 42)


@pytest.mark.asyncio
async def test_telegram_message_wraps_upstream_failure():
    class FailingMessageClient:
        async def get_messages(self, channel, ids):
            raise RuntimeError("telegram unavailable")

    main_module.telegram_client = FailingMessageClient()

    with pytest.raises(main_module.FastMCPError, match="메시지 조회 중 Telegram 오류"):
        await _tool_callable("get_telegram_message")("watcherguru", 42)


def _resolved(value):
    async def _inner():
        return value

    return _inner()


def _dt():
    return datetime.now(timezone.utc)
