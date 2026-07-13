from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_deploy_requires_main_branch():
    workflow = (ROOT / ".github" / "workflows" / "docker-cicd.yml").read_text()

    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "github.event_name == 'push'" in workflow


def test_dev_build_waits_for_tests_without_stale_container_steps():
    workflow = (ROOT / ".github" / "workflows" / "dev-docker-cicd.yml").read_text()

    assert "  test:" in workflow
    assert "  build:\n    needs: test" in workflow
    assert "uv run pytest -q" in workflow
    assert "uv run python -m compileall src example scripts tests" in workflow
    assert "myapp-container" not in workflow
