import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sandbox.backend import DockerSandbox
from ecommerce_agent.sandbox.remote import RemoteSandboxClient
from ecommerce_agent.sessions.factory import build_session_sandbox


def test_factory_returns_docker_sandbox_by_default() -> None:
    settings = Settings(_env_file=None)

    sandbox = build_session_sandbox(settings, session_id="abc")

    assert isinstance(sandbox, DockerSandbox)


def test_factory_returns_remote_client_when_backend_remote() -> None:
    settings = Settings(
        _env_file=None,
        sandbox_backend="remote",
        sandbox_executor_url="http://executor:8000",
        sandbox_executor_token="tok",
    )

    sandbox = build_session_sandbox(settings, session_id="abc")

    assert isinstance(sandbox, RemoteSandboxClient)
    assert sandbox.id == "remote-abc"


def test_factory_accepts_backend_case_and_whitespace() -> None:
    settings = Settings(
        _env_file=None,
        sandbox_backend=" Remote ",
        sandbox_executor_url="http://executor:8000",
        sandbox_executor_token="tok",
    )

    sandbox = build_session_sandbox(settings, session_id="abc")

    assert isinstance(sandbox, RemoteSandboxClient)


def test_factory_docker_sandbox_carries_session_id() -> None:
    settings = Settings(_env_file=None)

    sandbox = build_session_sandbox(settings, session_id="sess-42")

    assert isinstance(sandbox, DockerSandbox)
    assert "sess-42" in sandbox.id


def test_factory_rejects_unknown_backend() -> None:
    settings = Settings(_env_file=None, sandbox_backend="remtoe")

    with pytest.raises(ValueError, match="sandbox_backend"):
        build_session_sandbox(settings, session_id="abc")


@pytest.mark.parametrize(
    ("url", "token", "message"),
    [
        ("", "tok", "sandbox_executor_url"),
        ("http://executor:8000", "", "sandbox_executor_token"),
    ],
)
def test_factory_rejects_incomplete_remote_config(
    url: str,
    token: str,
    message: str,
) -> None:
    settings = Settings(
        _env_file=None,
        sandbox_backend="remote",
        sandbox_executor_url=url,
        sandbox_executor_token=token,
    )

    with pytest.raises(ValueError, match=message):
        build_session_sandbox(settings, session_id="abc")
