from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def test_ci_covers_main_dev_and_pull_requests() -> None:
    workflow = _workflow_text()

    assert "branches: [main, dev]" in workflow
    assert "pull_request:\n    branches: [main]" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "cancel-in-progress: true" in workflow


def test_quality_job_gates_image_build() -> None:
    workflow = _workflow_text()

    quality = workflow.index("  quality:")
    lint = workflow.index("uv run ruff check .")
    format_check = workflow.index("uv run ruff format --check .")
    type_check = workflow.index("uv run pyright")
    tests = workflow.index("uv run pytest -q")
    compile_sources = workflow.index("uv run python -m compileall -q src example scripts tests")
    build = workflow.index("  build:\n    needs: quality")

    assert quality < lint < format_check < type_check < tests < compile_sources < build


def test_production_deploy_requires_main_push_or_manual_dispatch() -> None:
    workflow = _workflow_text()

    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "github.event_name == 'push'" in workflow


def test_production_deploy_writes_owner_only_environment_file() -> None:
    workflow = _workflow_text()

    assert "install -m 600 /dev/null .env" in workflow
    assert "envs: DEPLOY_VERSION,DEPLOY_COINGECKO_API_KEY" in workflow
    assert "printf 'TELEGRAM_SESSION_STRING=%s\\n'" in workflow
    assert 'echo "TELEGRAM_SESSION_STRING=${{ secrets.' not in workflow


def test_production_build_succeeds_before_service_replacement() -> None:
    workflow = _workflow_text()

    fail_fast = workflow.index("set -eu")
    validate = workflow.index("docker compose config --quiet")
    build = workflow.index("docker compose build --no-cache")
    replace = workflow.index("docker compose up -d --no-build --remove-orphans")

    assert fail_fast < validate < build < replace
    assert "docker compose down --rmi all" not in workflow


def test_ci_uses_project_name_and_checks_non_root_runtime() -> None:
    workflow = _workflow_text()

    assert "myapp" not in workflow
    assert "--tag crypto-info-mcp:" in workflow
    assert "--name crypto-info-mcp-ci" in workflow
    assert "assert os.geteuid() != 0" in workflow
    assert "! command -v uv" in workflow


def test_dockerfile_is_minimal_pinned_and_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "ghcr.io/astral-sh/uv:0.9.16@sha256:" in dockerfile
    assert "python:3.11-slim-bookworm@sha256:" in dockerfile
    assert ":latest" not in dockerfile
    assert "apt-get" not in dockerfile
    assert "FROM python:3.11-slim-bookworm" in dockerfile
    assert " AS builder" in dockerfile
    assert " AS runtime" in dockerfile
    assert "COPY --from=builder --chown=app:app /usr/src/app/.venv ./.venv" in dockerfile
    assert "COPY --chown=app:app src ./src" in dockerfile
    assert "USER app" in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile


def test_compose_passes_only_server_configuration_and_hardens_runtime() -> None:
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
    assert "read_only: true" in compose
    assert "cap_drop:\n      - ALL" in compose
    assert "no-new-privileges:true" in compose
    assert "container_name:" not in compose
