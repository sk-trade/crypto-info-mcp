import asyncio
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


def _tool_result_text(result):
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


async def _assert_crypto_error(awaitable, code):
    with pytest.raises(main_module.CryptoToolError) as captured:
        await awaitable
    assert captured.value.code is code
    assert str(captured.value) == f"{code.value}: {captured.value.message}"
    return captured.value


@pytest.fixture(autouse=True)
def reset_runtime_state(monkeypatch):
    monkeypatch.setattr(main_module, "telegram_client", None)
    monkeypatch.setattr(
        main_module,
        "telegram_availability",
        main_module.TelegramAvailability.NOT_CONFIGURED,
    )
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


class FailingNewsMCPTestTelegramClient(AuthorizedStartupTelegramClient):
    async def iter_messages(self, channel, **kwargs):
        raise RuntimeError("channel unavailable")
        yield


@pytest.mark.asyncio
async def test_market_overview_combines_available_sources(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_ok({"value": "70", "value_classification": "Greed"})),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_ok({"market_cap_percentage": {"btc": 52.3, "eth": 18.1}})),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.OK,
            ("Whale moved 1,000 BTC",),
        )),
    )

    report = await _tool_callable("get_market_overview")()

    assert "Greed" in report
    assert "BTC 52.3%" in report
    assert "Whale moved 1,000 BTC" in report


@pytest.mark.asyncio
async def test_market_overview_reports_no_whale_movement_when_telegram_unavailable(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_ok({"value": "55", "value_classification": "Neutral"})),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_ok({"market_cap_percentage": {"btc": 48.0, "eth": 16.0}})),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.NO_MESSAGES,
        )),
    )

    report = await _tool_callable("get_market_overview")()

    assert "포착된 움직임 없음" in report


@pytest.mark.asyncio
async def test_market_overview_treats_unconfigured_telegram_as_no_whale_movement(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_ok({"value": "55", "value_classification": "Neutral"})),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_ok({"market_cap_percentage": {"btc": 48.0, "eth": 16.0}})),
    )

    report = await _tool_callable("get_market_overview")()

    assert "Telegram이 설정되지 않아 확인 불가" in report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (main_module.TelegramFetchStatus.UNAUTHORIZED, "Telegram 인증에 실패하여 확인 불가"),
        (main_module.TelegramFetchStatus.UNAVAILABLE, "Telegram을 사용할 수 없어 확인 불가"),
        (main_module.TelegramFetchStatus.FETCH_FAILED, "Telegram 조회 실패로 확인 불가"),
        (main_module.TelegramFetchStatus.NO_MESSAGES, "최근 1시간 내 포착된 움직임 없음"),
    ],
)
async def test_market_overview_distinguishes_telegram_availability_states(monkeypatch, status, expected):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_unavailable()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_not_configured()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(status)),
    )

    report = await _tool_callable("get_market_overview")()

    assert expected in report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("percentages", "expected"),
    [
        ({"btc": None, "eth": "unknown"}, "BTC N/A, ETH N/A"),
        ({"btc": 52.3, "eth": None}, "BTC 52.3%, ETH N/A"),
    ],
)
async def test_market_overview_tolerates_incomplete_dominance_payload(monkeypatch, percentages, expected):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_unavailable()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_ok({"market_cap_percentage": percentages})),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.NO_MESSAGES,
        )),
    )

    report = await _tool_callable("get_market_overview")()

    assert expected in report


@pytest.mark.asyncio
async def test_market_overview_reports_unavailable_market_sources(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_unavailable()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_unavailable()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.NO_MESSAGES,
        )),
    )

    report = await _tool_callable("get_market_overview")()

    assert report.startswith("현재 시장 개요 브리핑:")
    assert "시장 심리: Alternative.me 조회 실패로 확인 불가" in report
    assert "시장 지배력: CoinGecko 조회 실패로 확인 불가" in report
    assert "포착된 움직임 없음" in report


@pytest.mark.asyncio
async def test_market_overview_omits_only_unconfigured_coingecko(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_unavailable()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_not_configured()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.NO_MESSAGES,
        )),
    )

    unconfigured_report = await _tool_callable("get_market_overview")()

    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_unavailable()),
    )
    unavailable_report = await _tool_callable("get_market_overview")()

    assert "시장 지배력" not in unconfigured_report
    assert "시장 지배력: CoinGecko 조회 실패로 확인 불가" in unavailable_report


@pytest.mark.asyncio
async def test_fetch_helpers_reject_malformed_data_shapes(monkeypatch):
    responses = iter(
        [
            FakeResponse({"data": "unexpected"}),
            FakeResponse({"data": ["unexpected"]}),
            FakeResponse({"data": []}),
            FakeResponse({"data": [{}]}),
            FakeResponse({"data": [{"error": "schema changed"}]}),
            FakeResponse({"data": [{
                "value": "",
                "value_classification": "Greed",
            }]}),
            FakeResponse({"data": [{
                "value": "not-a-number",
                "value_classification": "Greed",
            }]}),
            FakeResponse({"data": [{
                "value": "101",
                "value_classification": "Greed",
            }]}),
            FakeResponse({"data": [{
                "value": "70",
                "value_classification": 123,
            }]}),
            FakeResponse({"data": "market_cap_percentage"}),
            FakeResponse({"data": {"market_cap_percentage": None}}),
            FakeResponse({"data": {"market_cap_percentage": {}}}),
        ]
    )
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda: FakeAsyncClient(next(responses)),
    )
    main_module.COINGECKO_API_KEY = "test-key"

    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_fear_and_greed_index() == _market_unavailable()
    assert await main_module._fetch_global_market_data() == _market_unavailable()
    assert await main_module._fetch_global_market_data() == _market_unavailable()
    assert await main_module._fetch_global_market_data() == _market_unavailable()


@pytest.mark.asyncio
async def test_fetch_market_helpers_return_structured_success(monkeypatch):
    fear_and_greed = {"value": "70", "value_classification": "Greed"}
    global_market = {"market_cap_percentage": {"btc": 52.3, "eth": 18.1}}
    responses = iter(
        [
            FakeResponse({"data": [fear_and_greed]}),
            FakeResponse({"data": global_market}),
        ]
    )
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda: FakeAsyncClient(next(responses)),
    )
    main_module.COINGECKO_API_KEY = "test-key"

    assert await main_module._fetch_fear_and_greed_index() == _market_ok(
        fear_and_greed
    )
    assert await main_module._fetch_global_market_data() == _market_ok(global_market)


@pytest.mark.asyncio
async def test_fetch_global_market_data_distinguishes_missing_configuration():
    assert await main_module._fetch_global_market_data() == _market_not_configured()


@pytest.mark.asyncio
async def test_fastmcp_market_overview_reports_both_upstream_failures(monkeypatch):
    class FailingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise RuntimeError("upstream unavailable")

    main_module.COINGECKO_API_KEY = "test-key"
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FailingAsyncClient)

    async with Client(main_module.mcp) as client:
        result = await client.call_tool(
            "get_market_overview",
            {},
            raise_on_error=False,
        )

    report = _tool_result_text(result)
    assert result.is_error is False
    assert "시장 심리: Alternative.me 조회 실패로 확인 불가" in report
    assert "시장 지배력: CoinGecko 조회 실패로 확인 불가" in report


@pytest.mark.asyncio
async def test_fastmcp_market_overview_rejects_unusable_http_200_payloads(monkeypatch):
    class MalformedAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            if "alternative.me" in url:
                return FakeResponse({"data": [{"error": "schema changed"}]})
            return FakeResponse({"data": {"market_cap_percentage": {}}})

    main_module.COINGECKO_API_KEY = "test-key"
    monkeypatch.setattr(main_module.httpx, "AsyncClient", MalformedAsyncClient)

    async with Client(main_module.mcp) as client:
        result = await client.call_tool(
            "get_market_overview",
            {},
            raise_on_error=False,
        )

    report = _tool_result_text(result)
    assert result.is_error is False
    assert "시장 심리: Alternative.me 조회 실패로 확인 불가" in report
    assert "시장 지배력: CoinGecko 조회 실패로 확인 불가" in report


@pytest.mark.asyncio
async def test_market_overview_reports_no_whale_movement_when_no_alerts(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_unavailable()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_not_configured()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.NO_MESSAGES,
        )),
    )

    report = await _tool_callable("get_market_overview")()

    assert "포착된 움직임 없음" in report


@pytest.mark.asyncio
async def test_market_overview_bounds_whale_alert_text(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_fetch_fear_and_greed_index",
        lambda: _resolved(_market_unavailable()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_not_configured()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.OK,
            ("x" * 100_000,),
        )),
    )

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
        lambda: _resolved(_market_ok({
            "value": "1" * 100_000,
            "value_classification": "x" * 100_000,
        })),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_global_market_data",
        lambda: _resolved(_market_not_configured()),
    )
    monkeypatch.setattr(
        main_module,
        "_fetch_whale_alerts",
        lambda: _resolved(main_module.WhaleAlertResult(
            main_module.TelegramFetchStatus.NO_MESSAGES,
        )),
    )

    report = await _tool_callable("get_market_overview")()

    assert report.count("...") == 2
    assert len(report) < 1_000


@pytest.mark.asyncio
async def test_coin_details_requires_api_key():
    await _assert_crypto_error(
        _tool_callable("get_coin_details")("bitcoin"),
        main_module.ToolErrorCode.COINGECKO_API_KEY_MISSING,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("coin_id", [None, "", "   "])
async def test_coin_details_rejects_missing_coin_id_without_request(monkeypatch, coin_id):
    class _FailingAsyncClient:
        def __init__(self):
            raise AssertionError("CoinGecko request should not be created")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: _FailingAsyncClient())

    await _assert_crypto_error(
        _tool_callable("get_coin_details")(coin_id),
        main_module.ToolErrorCode.COIN_ID_REQUIRED,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("coin_id", ["x" * 201, "bitcoin/market", "bitcoin?x=1", "bitcoin.coin"])
async def test_coin_details_rejects_unsafe_or_oversized_coin_id_without_request(monkeypatch, coin_id):
    class _FailingAsyncClient:
        def __init__(self):
            raise AssertionError("CoinGecko request should not be created")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: _FailingAsyncClient())

    error = await _assert_crypto_error(
        _tool_callable("get_coin_details")(coin_id),
        main_module.ToolErrorCode.COIN_ID_INVALID,
    )

    assert coin_id not in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coin_id", "normalized_id"),
    [
        ("  bitcoin  ", "bitcoin"),
        ("croak_on_linea", "croak_on_linea"),
        ("aaai_agent-by-virtuals", "aaai_agent-by-virtuals"),
        ("_", "_"),
        ("-11", "-11"),
    ],
)
async def test_coin_details_accepts_and_normalizes_safe_coin_ids(
    monkeypatch,
    coin_id,
    normalized_id,
):
    main_module.COINGECKO_API_KEY = "test-key"
    requested_urls = []

    class RecordingAsyncClient(FakeAsyncClient):
        async def get(self, url, **kwargs):
            requested_urls.append(url)
            return await super().get(url, **kwargs)

    response = FakeResponse({"name": "Bitcoin", "symbol": "btc"})
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: RecordingAsyncClient(response))

    await _tool_callable("get_coin_details")(coin_id)

    assert requested_urls == [
        f"https://api.coingecko.com/api/v3/coins/{normalized_id}"
        "?localization=false&tickers=false&community_data=false&developer_data=false"
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
async def test_fastmcp_client_preserves_coin_detail_contract(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    responses = iter(
        [
            FakeResponse(
                {
                    "name": "Bitcoin",
                    "symbol": "btc",
                    "market_cap_rank": 1,
                    "market_data": {"current_price": {"krw": 123456789}},
                    "links": {"homepage": ["https://bitcoin.org"]},
                }
            ),
            FakeResponse({}, status_code=404),
            FakeResponse({}),
        ]
    )
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda: FakeAsyncClient(next(responses)),
    )

    async with Client(main_module.mcp) as client:
        success = await client.call_tool(
            "get_coin_details",
            {"coin_id": "bitcoin"},
            raise_on_error=False,
        )
        blank_id = await client.call_tool(
            "get_coin_details",
            {"coin_id": "   "},
            raise_on_error=False,
        )
        omitted_id = await client.call_tool(
            "get_coin_details",
            {},
            raise_on_error=False,
        )
        null_id = await client.call_tool(
            "get_coin_details",
            {"coin_id": None},
            raise_on_error=False,
        )
        missing_coin = await client.call_tool(
            "get_coin_details",
            {"coin_id": "missing-coin"},
            raise_on_error=False,
        )
        invalid_payload = await client.call_tool(
            "get_coin_details",
            {"coin_id": "invalid-payload"},
            raise_on_error=False,
        )

    success_text = "\n".join(
        block.text for block in success.content if getattr(block, "text", None)
    )
    blank_id_text = "\n".join(
        block.text for block in blank_id.content if getattr(block, "text", None)
    )
    omitted_id_text = "\n".join(
        block.text for block in omitted_id.content if getattr(block, "text", None)
    )
    null_id_text = "\n".join(
        block.text for block in null_id.content if getattr(block, "text", None)
    )
    missing_coin_text = "\n".join(
        block.text for block in missing_coin.content if getattr(block, "text", None)
    )
    invalid_payload_text = "\n".join(
        block.text for block in invalid_payload.content if getattr(block, "text", None)
    )
    assert success.is_error is False
    assert "Bitcoin" in success_text
    assert "BTC" in success_text
    assert "₩123,456,789" in success_text
    assert blank_id.is_error is True
    assert blank_id_text.partition(": ")[0] == main_module.ToolErrorCode.COIN_ID_REQUIRED.value
    assert omitted_id.is_error is True
    assert omitted_id_text.partition(": ")[0] == main_module.ToolErrorCode.COIN_ID_REQUIRED.value
    assert null_id.is_error is True
    assert null_id_text.partition(": ")[0] == main_module.ToolErrorCode.COIN_ID_REQUIRED.value
    assert missing_coin.is_error is True
    assert missing_coin_text.partition(": ")[0] == main_module.ToolErrorCode.COIN_NOT_FOUND.value
    assert invalid_payload.is_error is True
    assert (
        invalid_payload_text.partition(": ")[0]
        == main_module.ToolErrorCode.COINGECKO_UPSTREAM_ERROR.value
    )


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
async def test_coin_details_uses_first_non_empty_homepage(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    response = FakeResponse(
        {
            "name": "Example coin",
            "links": {"homepage": ["", "https://example.invalid"]},
        }
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeAsyncClient(response))

    report = await _tool_callable("get_coin_details")("example")

    assert "홈페이지: https://example.invalid" in report


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

    await _assert_crypto_error(
        _tool_callable("get_coin_details")("missing-coin"),
        main_module.ToolErrorCode.COIN_NOT_FOUND,
    )


@pytest.mark.asyncio
async def test_coin_details_maps_non_404_http_error_without_exposing_upstream_details(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda: FakeAsyncClient(FakeResponse({}, status_code=500)),
    )

    error = await _assert_crypto_error(
        _tool_callable("get_coin_details")("bitcoin"),
        main_module.ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
    )

    assert "500" not in str(error)


@pytest.mark.asyncio
async def test_coin_details_maps_generic_failure_without_exposing_upstream_details(monkeypatch):
    main_module.COINGECKO_API_KEY = "test-key"

    class InvalidJsonResponse(FakeResponse):
        def json(self):
            raise RuntimeError("private upstream detail")

    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda: FakeAsyncClient(InvalidJsonResponse({})),
    )

    error = await _assert_crypto_error(
        _tool_callable("get_coin_details")("bitcoin"),
        main_module.ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
    )

    assert "private upstream detail" not in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"error": "rate limited"},
        {"name": "   "},
        {"name": 123},
    ],
)
async def test_coin_details_rejects_unidentifiable_success_payload(monkeypatch, payload):
    main_module.COINGECKO_API_KEY = "test-key"
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda: FakeAsyncClient(FakeResponse(payload)),
    )

    await _assert_crypto_error(
        _tool_callable("get_coin_details")("bitcoin"),
        main_module.ToolErrorCode.COINGECKO_UPSTREAM_ERROR,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [0, 73, "1", True, 1.0, None])
async def test_realtime_news_rejects_invalid_hours(hours):
    await _assert_crypto_error(
        _tool_callable("get_realtime_news")(hours),
        main_module.ToolErrorCode.NEWS_HOURS_INVALID,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_realtime_news", {"hours": "1"}),
        ("get_realtime_news", {"hours": True}),
        ("get_realtime_news", {"hours": 1.0}),
        (
            "get_telegram_message",
            {"channel": "watcherguru", "message_id": "1"},
        ),
        (
            "get_telegram_message",
            {"channel": "watcherguru", "message_id": True},
        ),
        (
            "get_telegram_message",
            {"channel": "watcherguru", "message_id": 1.0},
        ),
    ],
)
async def test_mcp_integer_parameters_reject_coercible_non_integers(
    tool_name,
    arguments,
):
    async with Client(main_module.mcp) as client:
        result = await client.call_tool(tool_name, arguments, raise_on_error=False)

    assert result.is_error is True
    assert result.content


@pytest.mark.asyncio
async def test_mcp_tool_schemas_publish_runtime_constraints():
    async with Client(main_module.mcp) as client:
        listed_tools = await client.list_tools()

    tools = {tool.name: tool.inputSchema for tool in listed_tools}
    outputs = {tool.name: tool.outputSchema for tool in listed_tools}

    coin_id = tools["get_coin_details"]["properties"]["coin_id"]
    assert tools["get_coin_details"]["required"] == ["coin_id"]
    assert coin_id["type"] == "string"
    assert "default" not in coin_id
    assert "anyOf" not in coin_id
    assert coin_id["minLength"] == 1
    assert coin_id["maxLength"] == main_module.COIN_ID_MAX_CHARS
    assert coin_id["pattern"] == main_module.COIN_ID_PATTERN.pattern

    hours = tools["get_realtime_news"]["properties"]["hours"]
    assert hours["minimum"] == 1
    assert hours["maximum"] == 72

    news_output = outputs["get_realtime_news"]
    assert set(news_output["required"]) == {"hours", "messages", "failed_channels"}
    item_ref = news_output["properties"]["messages"]["items"]["$ref"]
    item_schema = news_output["$defs"][item_ref.rsplit("/", 1)[-1]]
    assert set(item_schema["required"]) == {
        "channel",
        "message_id",
        "timestamp",
        "preview",
        "truncated",
    }

    message = tools["get_telegram_message"]["properties"]
    assert set(message["channel"]["enum"]) == main_module.ALLOWED_TELEGRAM_CHANNELS
    assert message["message_id"]["minimum"] == 1


@pytest.mark.asyncio
async def test_telegram_client_helper_raises_when_disabled():
    await _assert_crypto_error(
        main_module._get_telegram_client(),
        main_module.ToolErrorCode.TELEGRAM_UNAVAILABLE,
    )


@pytest.mark.asyncio
async def test_lifespan_disables_telegram_when_startup_connection_fails(monkeypatch):
    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", FailingStartupTelegramClient)

    async with main_module.lifespan(None):
        assert main_module.telegram_client is None
        assert main_module.telegram_availability is main_module.TelegramAvailability.UNAVAILABLE


@pytest.mark.asyncio
async def test_lifespan_disables_telegram_when_startup_times_out(monkeypatch):
    class HangingStartupTelegramClient:
        async def connect(self):
            await asyncio.sleep(1)

        async def is_user_authorized(self):
            return True

        def is_connected(self):
            return False

    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    monkeypatch.setattr(main_module, "TELEGRAM_OPERATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: HangingStartupTelegramClient())

    async with main_module.lifespan(None):
        assert main_module.telegram_client is None
        assert main_module.telegram_availability is main_module.TelegramAvailability.UNAVAILABLE


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
        assert main_module.telegram_availability is main_module.TelegramAvailability.UNAUTHORIZED

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
        assert main_module.telegram_availability is main_module.TelegramAvailability.AVAILABLE

    assert fake_client.disconnected is True
    assert main_module.telegram_client is None
    assert main_module.telegram_availability is main_module.TelegramAvailability.UNAVAILABLE


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
async def test_lifespan_bounds_authorized_cleanup_time(monkeypatch):
    class HangingDisconnectTelegramClient(AuthorizedStartupTelegramClient):
        async def disconnect(self):
            self.disconnected = True
            await asyncio.sleep(1)

    main_module.TELEGRAM_API_ID = "123"
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    fake_client = HangingDisconnectTelegramClient()
    monkeypatch.setattr(main_module, "TELEGRAM_CLEANUP_TIMEOUT_SECONDS", 0.01)
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
        reference = news_result.structured_content["messages"][0]
        message_result = await client.call_tool(
            "get_telegram_message",
            {
                "channel": reference["channel"],
                "message_id": reference["message_id"],
            },
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
    assert set(reference) == {
        "channel",
        "message_id",
        "timestamp",
        "preview",
        "truncated",
    }
    assert reference["channel"] == "wublockchainenglish"
    assert reference["message_id"] == 42
    assert reference["timestamp"].endswith(" UTC")
    assert reference["preview"] == "protocol news preview"
    assert reference["truncated"] is False
    assert message_result.is_error is False
    assert message_text == "protocol full message"
    assert fake_client.disconnected is True
    assert main_module.telegram_client is None


@pytest.mark.asyncio
async def test_fastmcp_client_reports_total_news_failure_as_error(monkeypatch):
    main_module.TELEGRAM_API_ID = 123
    main_module.TELEGRAM_API_HASH = "hash"
    main_module.TELEGRAM_SESSION_STRING = "session"
    fake_client = FailingNewsMCPTestTelegramClient()
    monkeypatch.setattr(main_module, "StringSession", lambda session: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: fake_client)

    async with Client(main_module.mcp) as client:
        result = await client.call_tool(
            "get_realtime_news",
            {"hours": 1},
            raise_on_error=False,
        )

    error_text = "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )
    assert result.is_error is True
    assert (
        error_text.partition(": ")[0]
        == main_module.ToolErrorCode.TELEGRAM_UPSTREAM_ERROR.value
    )
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

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)

    assert "지난 1시간 동안의 주요 뉴스" in report
    assert "@wublockchainenglish #1" in report
    assert "line1 line2" in report
    message = result.structured_content["messages"][0]
    assert message["channel"] == "wublockchainenglish"
    assert message["message_id"] == 1
    assert message["timestamp"].endswith(" UTC")
    assert message["preview"] == "line1 line2"
    assert message["truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_preview", "truncated", "marker_present"),
    [
        ("x" * 150, "x" * 150, False, False),
        ("x" * 151, "x" * 147 + "...", True, True),
    ],
)
async def test_realtime_news_bounds_preview_at_150_characters(
    text,
    expected_preview,
    truncated,
    marker_present,
):
    main_module.telegram_client = FakeTelegramClient(
        {
            "wublockchainenglish": [FakeMessage(text, date=_dt())],
            "watcherguru": [],
        }
    )

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)
    message = result.structured_content["messages"][0]

    assert message["preview"] == expected_preview
    assert message["truncated"] is truncated
    assert ("(원문 참조 필요)" in report) is marker_present


@pytest.mark.asyncio
async def test_realtime_news_accepts_inclusive_72_hour_boundary():
    main_module.telegram_client = FakeTelegramClient(
        {
            "wublockchainenglish": [],
            "watcherguru": [],
        }
    )

    result = await _tool_callable("get_realtime_news")(72)

    assert result.structured_content == {
        "hours": 72,
        "messages": [],
        "failed_channels": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", [None, 0, True, "1"])
async def test_realtime_news_rejects_unusable_message_references(message_id):
    result = await main_module._fetch_news_channel(
        FakeTelegramClient({
            "wublockchainenglish": [
                FakeMessage("unusable reference", _dt(), message_id=message_id),
            ],
        }),
        "wublockchainenglish",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )

    assert result.messages == ()
    assert result.failure is main_module.NewsChannelFailureCode.INVALID_MESSAGE_REFERENCE


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

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)

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

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)

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

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)

    assert "available news" in report
    assert "조회 실패 채널" in report
    assert "@watcherguru" in report
    assert "channel unavailable" not in report
    assert result.structured_content["failed_channels"] == [
        {
            "channel": "watcherguru",
            "code": "upstream_error",
            "message": "채널을 조회할 수 없습니다.",
        }
    ]


@pytest.mark.asyncio
async def test_realtime_news_reports_no_news_when_other_channel_fails():
    class EmptyAndFailingTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            if channel == "watcherguru":
                raise RuntimeError("channel unavailable")
            if False:
                yield

    main_module.telegram_client = EmptyAndFailingTelegramClient()

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)

    assert report.startswith(
        "지난 1시간 동안 조회에 성공한 채널에서는 새로운 뉴스가 없습니다."
    )
    assert "조회 실패 채널" in report
    assert "@watcherguru" in report
    assert result.structured_content == {
        "hours": 1,
        "messages": [],
        "failed_channels": [
            {
                "channel": "watcherguru",
                "code": "upstream_error",
                "message": "채널을 조회할 수 없습니다.",
            }
        ],
    }


@pytest.mark.asyncio
async def test_realtime_news_errors_when_all_channels_fail():
    class FailingTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            raise RuntimeError("channel unavailable")
            yield

    main_module.telegram_client = FailingTelegramClient()

    await _assert_crypto_error(
        _tool_callable("get_realtime_news")(1),
        main_module.ToolErrorCode.TELEGRAM_UPSTREAM_ERROR,
    )


@pytest.mark.asyncio
async def test_realtime_news_does_not_expose_unbounded_channel_errors():
    class PartiallyFailingTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            if channel == "watcherguru":
                raise RuntimeError("e" * 100_000)
            yield FakeMessage("available news", _dt())

    main_module.telegram_client = PartiallyFailingTelegramClient()

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)

    assert "available news" in report
    assert "@watcherguru" in report
    assert "e" * 1_000 not in report
    assert len(report) < 1_000


@pytest.mark.asyncio
async def test_realtime_news_reports_channel_timeouts(monkeypatch):
    class HangingTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            await asyncio.sleep(1)
            yield

    monkeypatch.setattr(main_module, "TELEGRAM_OPERATION_TIMEOUT_SECONDS", 0.01)
    result = await main_module._fetch_news_channel(
        HangingTelegramClient(),
        "watcherguru",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )

    assert result.messages == ()
    assert result.failure is main_module.NewsChannelFailureCode.TIMEOUT


@pytest.mark.asyncio
async def test_realtime_news_errors_when_all_channels_timeout(monkeypatch):
    class HangingTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            await asyncio.sleep(1)
            yield

    monkeypatch.setattr(main_module, "TELEGRAM_OPERATION_TIMEOUT_SECONDS", 0.01)
    main_module.telegram_client = HangingTelegramClient()

    await _assert_crypto_error(
        _tool_callable("get_realtime_news")(1),
        main_module.ToolErrorCode.TELEGRAM_TIMEOUT,
    )


@pytest.mark.asyncio
async def test_realtime_news_prefers_upstream_error_for_mixed_total_failure(monkeypatch):
    class MixedFailureTelegramClient:
        async def iter_messages(self, channel, **kwargs):
            if channel == "wublockchainenglish":
                await asyncio.sleep(1)
            else:
                raise RuntimeError("channel unavailable")
            if False:
                yield

    monkeypatch.setattr(main_module, "TELEGRAM_OPERATION_TIMEOUT_SECONDS", 0.01)
    main_module.telegram_client = MixedFailureTelegramClient()

    await _assert_crypto_error(
        _tool_callable("get_realtime_news")(1),
        main_module.ToolErrorCode.TELEGRAM_UPSTREAM_ERROR,
    )


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
    main_module.telegram_availability = main_module.TelegramAvailability.AVAILABLE

    result = await main_module._fetch_whale_alerts()

    assert result == main_module.WhaleAlertResult(
        main_module.TelegramFetchStatus.OK,
        ("recent whale",),
    )
    assert client.calls == [("whale_alert_io", {"limit": 5})]


@pytest.mark.asyncio
async def test_whale_alerts_reports_unauthorized_state(monkeypatch):
    monkeypatch.setattr(main_module, "TELEGRAM_API_ID", 1)
    monkeypatch.setattr(main_module, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(main_module, "TELEGRAM_SESSION_STRING", "session")
    main_module.telegram_availability = main_module.TelegramAvailability.UNAUTHORIZED

    assert await main_module._fetch_whale_alerts() == main_module.WhaleAlertResult(
        main_module.TelegramFetchStatus.UNAUTHORIZED,
    )


@pytest.mark.asyncio
async def test_whale_alerts_reports_unavailable_when_initialization_failed():
    main_module.telegram_availability = main_module.TelegramAvailability.UNAVAILABLE

    assert await main_module._fetch_whale_alerts() == main_module.WhaleAlertResult(
        main_module.TelegramFetchStatus.UNAVAILABLE,
    )


@pytest.mark.asyncio
async def test_whale_alerts_reports_fetch_failure(monkeypatch):
    class FailingWhaleClient:
        async def iter_messages(self, channel, **kwargs):
            raise RuntimeError("channel unavailable")
            yield

    monkeypatch.setattr(main_module, "TELEGRAM_API_ID", 1)
    monkeypatch.setattr(main_module, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(main_module, "TELEGRAM_SESSION_STRING", "session")
    main_module.telegram_client = FailingWhaleClient()
    main_module.telegram_availability = main_module.TelegramAvailability.AVAILABLE

    assert await main_module._fetch_whale_alerts() == main_module.WhaleAlertResult(
        main_module.TelegramFetchStatus.FETCH_FAILED,
    )


@pytest.mark.asyncio
async def test_whale_alerts_reports_no_messages(monkeypatch):
    monkeypatch.setattr(main_module, "TELEGRAM_API_ID", 1)
    monkeypatch.setattr(main_module, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(main_module, "TELEGRAM_SESSION_STRING", "session")
    main_module.telegram_client = FakeTelegramClient({"whale_alert_io": []})
    main_module.telegram_availability = main_module.TelegramAvailability.AVAILABLE

    assert await main_module._fetch_whale_alerts() == main_module.WhaleAlertResult(
        main_module.TelegramFetchStatus.NO_MESSAGES,
    )


@pytest.mark.asyncio
async def test_whale_alerts_reports_timeout(monkeypatch):
    class HangingWhaleClient:
        async def iter_messages(self, channel, **kwargs):
            await asyncio.sleep(1)
            yield

    monkeypatch.setattr(main_module, "TELEGRAM_API_ID", 1)
    monkeypatch.setattr(main_module, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(main_module, "TELEGRAM_SESSION_STRING", "session")
    monkeypatch.setattr(main_module, "TELEGRAM_OPERATION_TIMEOUT_SECONDS", 0.01)
    main_module.telegram_client = HangingWhaleClient()
    main_module.telegram_availability = main_module.TelegramAvailability.AVAILABLE

    assert await main_module._fetch_whale_alerts() == main_module.WhaleAlertResult(
        main_module.TelegramFetchStatus.FETCH_FAILED,
    )


@pytest.mark.asyncio
async def test_realtime_news_reports_clear_no_news_when_channels_are_empty():
    main_module.telegram_client = FakeTelegramClient(
        {
            "wublockchainenglish": [],
            "watcherguru": [],
        }
    )

    result = await _tool_callable("get_realtime_news")(1)
    report = _tool_result_text(result)

    assert report == "지난 1시간 동안 지정된 채널에서 새로운 뉴스가 없습니다."
    assert result.structured_content == {
        "hours": 1,
        "messages": [],
        "failed_channels": [],
    }


@pytest.mark.asyncio
async def test_realtime_news_errors_when_telegram_disabled():
    await _assert_crypto_error(
        _tool_callable("get_realtime_news")(1),
        main_module.ToolErrorCode.TELEGRAM_UNAVAILABLE,
    )


@pytest.mark.asyncio
async def test_telegram_message_rejects_disallowed_channel_before_client_lookup():
    await _assert_crypto_error(
        _tool_callable("get_telegram_message")("not-allowed", 1),
        main_module.ToolErrorCode.TELEGRAM_CHANNEL_NOT_ALLOWED,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", [0, -1, "1", True, 1.0, None])
async def test_telegram_message_rejects_invalid_message_id_before_client_lookup(message_id):
    await _assert_crypto_error(
        _tool_callable("get_telegram_message")("watcherguru", message_id),
        main_module.ToolErrorCode.TELEGRAM_MESSAGE_ID_INVALID,
    )


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
    await _assert_crypto_error(
        _tool_callable("get_telegram_message")("watcherguru", 42),
        main_module.ToolErrorCode.TELEGRAM_MESSAGE_NOT_FOUND,
    )

    main_module.telegram_client = FakeMessageClient(FakeMessage(None, _dt()))
    await _assert_crypto_error(
        _tool_callable("get_telegram_message")("watcherguru", 42),
        main_module.ToolErrorCode.TELEGRAM_MESSAGE_NOT_TEXT,
    )


@pytest.mark.asyncio
async def test_telegram_message_wraps_upstream_failure():
    class FailingMessageClient:
        async def get_messages(self, channel, ids):
            raise RuntimeError("telegram unavailable")

    main_module.telegram_client = FailingMessageClient()

    await _assert_crypto_error(
        _tool_callable("get_telegram_message")("watcherguru", 42),
        main_module.ToolErrorCode.TELEGRAM_UPSTREAM_ERROR,
    )


@pytest.mark.asyncio
async def test_telegram_message_reports_timeout(monkeypatch):
    class HangingMessageClient:
        async def get_messages(self, channel, ids):
            await asyncio.sleep(1)

    monkeypatch.setattr(main_module, "TELEGRAM_OPERATION_TIMEOUT_SECONDS", 0.01)
    main_module.telegram_client = HangingMessageClient()

    await _assert_crypto_error(
        _tool_callable("get_telegram_message")("watcherguru", 42),
        main_module.ToolErrorCode.TELEGRAM_TIMEOUT,
    )


def test_run_converts_keyboard_interrupt_to_clean_exit(monkeypatch, capsys):
    run_arguments = {}

    def interrupted_run(**kwargs):
        run_arguments.update(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module.mcp, "run", interrupted_run)

    assert main_module.run() == 0
    assert run_arguments == {
        "transport": "streamable-http",
        "host": "0.0.0.0",
        "port": 8123,
        "uvicorn_config": {"timeout_graceful_shutdown": 5},
    }
    assert "서버를 안전하게 종료했습니다." in capsys.readouterr().out


def _resolved(value):
    async def _inner():
        return value

    return _inner()


def _market_ok(data):
    return main_module.MarketSourceResult(main_module.MarketSourceStatus.OK, data)


def _market_unavailable():
    return main_module.MarketSourceResult(main_module.MarketSourceStatus.UNAVAILABLE)


def _market_not_configured():
    return main_module.MarketSourceResult(main_module.MarketSourceStatus.NOT_CONFIGURED)


def _dt():
    return datetime.now(timezone.utc)
