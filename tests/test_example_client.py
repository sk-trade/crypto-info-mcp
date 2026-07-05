import builtins
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
