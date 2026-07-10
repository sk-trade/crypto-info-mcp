import builtins
import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("example_client_module", ROOT / "example" / "client.py")
example_client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example_client)


def test_example_client_help_does_not_require_gemini_dependency():
    result = subprocess.run(
        [sys.executable, "example/client.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--host" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_load_gemini_reports_install_command_when_dependency_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("google.generativeai"):
            raise ModuleNotFoundError("No module named 'google'", name="google")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="google-generativeai"):
        example_client._load_gemini()


def test_main_returns_1_when_cleanup_raises_after_connect_failure(monkeypatch, capsys):
    real_cleanup = example_client.CryptoAssistantClient.cleanup

    class FailingContext:
        async def __aexit__(self, exc_type, exc, tb):
            raise RuntimeError("cleanup failed")

    class FakeClient:
        def __init__(self):
            self._session_context = FailingContext()
            self._streams_context = None

        async def connect(self, server_url):
            raise RuntimeError("connect failed")

        async def chat_loop(self):
            raise AssertionError("chat_loop should not run when connect fails")

    monkeypatch.setattr(sys, "argv", ["client.py"])
    monkeypatch.setattr(example_client, "CryptoAssistantClient", FakeClient)
    monkeypatch.setattr(FakeClient, "cleanup", real_cleanup, raising=False)

    result = asyncio.run(example_client.main())
    captured = capsys.readouterr()

    assert result == 1
    assert "클라이언트 시작 중 심각한 오류 발생: connect failed" in captured.out
    assert "⚠️ 클라이언트 정리 경고: 세션 종료 실패: cleanup failed" in captured.out
    assert "Traceback" not in captured.err


def test_process_query_handles_multiple_consecutive_tool_call_turns(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.calls = []

        async def list_tools(self):
            return SimpleNamespace(tools=[])

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return SimpleNamespace(content=[SimpleNamespace(text=f"{name} result")])

    class FakeChat:
        def __init__(self):
            self.messages = []
            self.responses = [
                _response(_function_call("market", {"region": "kr"}), _function_call("news", {})),
                _response(_function_call("details", {"coin_id": "bitcoin"})),
                _response(text="final answer"),
            ]

        def send_message(self, message, **kwargs):
            self.messages.append((message, kwargs))
            return self.responses.pop(0)

    client = object.__new__(example_client.CryptoAssistantClient)
    client.session = FakeSession()
    client.chat = FakeChat()
    client._mcp_tools_to_gemini_tools = lambda tools: []

    answer = asyncio.run(client.process_query("시장 분석"))

    assert answer == "final answer"
    assert client.session.calls == [
        ("market", {"region": "kr"}),
        ("news", {}),
        ("details", {"coin_id": "bitcoin"}),
    ]
    assert len(client.chat.messages) == 3
    assert len(client.chat.messages[1][0]) == 2


def test_process_query_stops_after_bounded_tool_call_turns():
    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[])

        async def call_tool(self, name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(text="result")])

    class FakeChat:
        def send_message(self, message, **kwargs):
            return _response(_function_call("loop", {}))

    client = object.__new__(example_client.CryptoAssistantClient)
    client.session = FakeSession()
    client.chat = FakeChat()
    client._mcp_tools_to_gemini_tools = lambda tools: []

    with pytest.raises(RuntimeError, match="more than 5 consecutive"):
        asyncio.run(client.process_query("계속 호출"))


def test_process_query_forwards_all_mcp_content_blocks_and_error_state():
    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[])

        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="first"),
                    SimpleNamespace(type="text", text="second"),
                ],
                isError=True,
            )

    class FakeChat:
        def __init__(self):
            self.messages = []
            self.responses = [_response(_function_call("news", {})), _response(text="final")]

        def send_message(self, message, **kwargs):
            self.messages.append(message)
            return self.responses.pop(0)

    client = object.__new__(example_client.CryptoAssistantClient)
    client.session = FakeSession()
    client.chat = FakeChat()
    client._mcp_tools_to_gemini_tools = lambda tools: []

    assert asyncio.run(client.process_query("뉴스")) == "final"
    response = client.chat.messages[1][0]["function_response"]["response"]
    assert response == {
        "content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ],
        "isError": True,
    }


def _function_call(name, arguments):
    return SimpleNamespace(name=name, args=arguments)


def _response(*function_calls, text=""):
    return SimpleNamespace(
        parts=[SimpleNamespace(function_call=function_call) for function_call in function_calls],
        text=text,
    )
