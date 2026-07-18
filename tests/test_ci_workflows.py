from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_deploy_requires_main_branch():
    workflow = (ROOT / ".github" / "workflows" / "docker-cicd.yml").read_text()

    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "github.event_name == 'push'" in workflow


def test_production_deploy_writes_owner_only_environment_file():
    workflow = (ROOT / ".github" / "workflows" / "docker-cicd.yml").read_text()

    assert "install -m 600 /dev/null .env" in workflow
    assert "envs: DEPLOY_VERSION,DEPLOY_COINGECKO_API_KEY" in workflow
    assert "printf 'TELEGRAM_SESSION_STRING=%s\\n'" in workflow
    assert 'echo "TELEGRAM_SESSION_STRING=${{ secrets.' not in workflow


def test_production_build_succeeds_before_service_replacement():
    workflow = (ROOT / ".github" / "workflows" / "docker-cicd.yml").read_text()

    fail_fast = workflow.index("set -e")
    build = workflow.index("docker compose build --no-cache")
    replace = workflow.index("docker compose up -d --no-build --remove-orphans")
    assert fail_fast < build < replace
    assert "docker compose down --rmi all" not in workflow


def test_dev_build_waits_for_tests_without_stale_container_steps():
    workflow = (ROOT / ".github" / "workflows" / "dev-docker-cicd.yml").read_text()

    assert "  test:" in workflow
    assert "  build:\n    needs: test" in workflow
    assert "uv run pytest -q" in workflow
    assert "uv run python -m compileall src example scripts tests" in workflow
    assert "myapp-container" not in workflow


def test_compose_passes_only_server_credentials():
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "env_file:" not in compose
    for name in (
        "COINGECKO_API_KEY",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION_STRING",
    ):
        assert f"{name}: ${{{name}:-}}" in compose
    assert "GOOGLE_API_KEY" not in compose
