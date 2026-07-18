import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.client.client import CallToolResult
from mcp.types import TextContent


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "smoke_client_module",
    ROOT / "example" / "smoke_client.py",
)
smoke_client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke_client)


class FakeClient:
    def __init__(self, tools, result):
        self.tools = tools
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def list_tools(self):
        return [SimpleNamespace(name=name) for name in self.tools]

    async def call_tool(self, name, arguments, raise_on_error):
        assert name == "get_market_overview"
        assert arguments == {}
        assert raise_on_error is False
        return self.result


def _result(text, is_error=False):
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=None,
        meta=None,
        is_error=is_error,
    )


@pytest.mark.asyncio
async def test_smoke_client_calls_overview_after_verifying_tools(monkeypatch, capsys):
    fake_client = FakeClient(smoke_client.REQUIRED_TOOLS, _result("시장 개요"))
    monkeypatch.setattr(smoke_client, "Client", lambda url: fake_client)

    assert await smoke_client.run_smoke("http://example.test/mcp") == 0

    output = capsys.readouterr().out
    assert "Connected to http://example.test/mcp" in output
    assert "get_telegram_message" in output
    assert "시장 개요" in output


@pytest.mark.asyncio
async def test_smoke_client_reports_missing_tools(monkeypatch, capsys):
    fake_client = FakeClient(
        smoke_client.REQUIRED_TOOLS - {"get_telegram_message"},
        _result("시장 개요"),
    )
    monkeypatch.setattr(smoke_client, "Client", lambda url: fake_client)

    assert await smoke_client.run_smoke("http://example.test/mcp") == 1
    assert "missing expected tool(s): get_telegram_message" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_smoke_client_preserves_tool_error(monkeypatch, capsys):
    fake_client = FakeClient(
        smoke_client.REQUIRED_TOOLS,
        _result("upstream unavailable", is_error=True),
    )
    monkeypatch.setattr(smoke_client, "Client", lambda url: fake_client)

    assert await smoke_client.run_smoke("http://example.test/mcp") == 1
    assert "upstream unavailable" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_smoke_client_rejects_empty_success(monkeypatch, capsys):
    fake_client = FakeClient(
        smoke_client.REQUIRED_TOOLS,
        _result("   "),
    )
    monkeypatch.setattr(smoke_client, "Client", lambda url: fake_client)

    assert await smoke_client.run_smoke("http://example.test/mcp") == 1
    assert "the tool returned no text" in capsys.readouterr().err
