import subprocess

import pytest

from ecommerce_agent.config import Settings
from tests.integration.helpers import skip_unless_docker_available


@pytest.mark.integration
@pytest.mark.docker
def test_sandbox_image_has_helpers_importable() -> None:
    skip_unless_docker_available()
    image = Settings(_env_file=None).sandbox_image

    check = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    if check.returncode != 0:
        pytest.skip(
            f"image {image} not built; run: "
            f"docker build -f sandbox_image/Dockerfile.sandbox -t {image} sandbox_image/"
        )

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            image,
            "python3",
            "-c",
            "import ecommerce_analysis, pandas, numpy; print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
