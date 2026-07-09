import builtins
import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys

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
