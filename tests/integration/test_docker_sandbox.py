import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sandbox.backend import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
from tests.integration.helpers import skip_unless_docker_available


@pytest.fixture
def sandbox():
    skip_unless_docker_available()
    import docker

    image = Settings(_env_file=None).sandbox_image
    try:
        docker.from_env().images.get(image)
    except Exception:
        pytest.skip(f"image {image} not built; run the sandbox image build")
    sandbox_backend = DockerSandbox(
        limits_from_settings(Settings(_env_file=None)),
        session_id=uuid.uuid4().hex[:8],
    )
    try:
        yield sandbox_backend
    finally:
        sandbox_backend.close()


@pytest.mark.integration
@pytest.mark.docker
def test_execute_runs_command_and_returns_output(sandbox) -> None:
    result = sandbox.execute("echo hello-sandbox")
    assert result.exit_code == 0
    assert "hello-sandbox" in result.output


@pytest.mark.integration
@pytest.mark.docker
def test_concurrent_first_use_creates_one_container(sandbox) -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda index: sandbox.execute(f"echo run-{index}"), range(4)))

    assert all(result.exit_code == 0 for result in results)
    assert all(f"run-{index}" in result.output for index, result in enumerate(results))


@pytest.mark.integration
@pytest.mark.docker
def test_id_is_stable(sandbox) -> None:
    assert sandbox.id == sandbox.id
    assert sandbox.id.startswith("ecommerce-sandbox-")


@pytest.mark.integration
@pytest.mark.docker
def test_files_persist_across_execute_calls(sandbox) -> None:
    write = sandbox.execute("echo persisted > /workspace/state.txt")
    assert write.exit_code == 0
    read = sandbox.execute("cat /workspace/state.txt")
    assert read.exit_code == 0
    assert "persisted" in read.output


@pytest.mark.integration
@pytest.mark.docker
def test_upload_then_download_roundtrip(sandbox) -> None:
    [upload] = sandbox.upload_files([("/workspace/data.json", b'{"k": 1}')])
    assert upload.error is None
    seen = sandbox.execute("cat /workspace/data.json")
    assert '"k": 1' in seen.output
    [download] = sandbox.download_files(["/workspace/data.json"])
    assert download.error is None
    assert download.content == b'{"k": 1}'


@pytest.mark.integration
@pytest.mark.docker
def test_file_transfer_accepts_relative_workspace_paths(sandbox) -> None:
    [upload] = sandbox.upload_files([("nested/data.txt", b"relative-ok")])
    assert upload.error is None
    seen = sandbox.execute("cat /workspace/nested/data.txt")
    assert "relative-ok" in seen.output
    [download] = sandbox.download_files(["nested/data.txt"])
    assert download.error is None
    assert download.content == b"relative-ok"


@pytest.mark.integration
@pytest.mark.docker
def test_file_transfer_rejects_paths_outside_workspace(sandbox) -> None:
    [upload] = sandbox.upload_files([("/tmp/data.txt", b"nope")])
    assert upload.error == "permission_denied"
    [download] = sandbox.download_files(["../data.txt"])
    assert download.error == "permission_denied"


@pytest.mark.integration
@pytest.mark.docker
def test_network_is_isolated(sandbox) -> None:
    code = (
        "import socket,sys\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1',53),timeout=3); print('REACHED')\n"
        "except Exception as e:\n"
        "    print('BLOCKED', type(e).__name__); sys.exit(0)\n"
    )
    [upload] = sandbox.upload_files([("/workspace/net.py", code.encode())])
    assert upload.error is None
    result = sandbox.execute("python3 /workspace/net.py")
    assert "BLOCKED" in result.output
    assert "REACHED" not in result.output


@pytest.mark.integration
@pytest.mark.docker
def test_execute_timeout_is_enforced(sandbox) -> None:
    result = sandbox.execute("sleep 10", timeout=1)
    assert result.exit_code == 124
    assert "timeout" in result.output.lower()


@pytest.mark.integration
@pytest.mark.docker
def test_helper_kit_is_importable_in_sandbox(sandbox) -> None:
    result = sandbox.execute("python3 -c 'import ecommerce_analysis; print(\"helpers-ok\")'")
    assert result.exit_code == 0
    assert "helpers-ok" in result.output


@pytest.mark.integration
@pytest.mark.docker
def test_close_removes_the_container(sandbox) -> None:
    sandbox.execute("true")
    import docker

    client = docker.from_env()
    assert client.containers.get(sandbox.id) is not None
    sandbox.close()
    with pytest.raises(docker.errors.NotFound):
        client.containers.get(sandbox.id)
