from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_deploy_requires_main_branch():
    workflow = (ROOT / ".github" / "workflows" / "docker-cicd.yml").read_text()

    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "github.event_name == 'push'" in workflow
