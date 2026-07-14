from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_workflow_publishes_multi_arch_patroni_image():
    workflow = (ROOT / ".github" / "workflows" / "docker-image.yml").read_text(
        encoding="utf-8"
    )

    assert "file: infra/patroni/Dockerfile" in workflow
    assert "platforms: linux/amd64,linux/arm64" in workflow
    assert "ghcr.io/lovegongqi/crm_barcode_patroni:16-4.1.4" in workflow
