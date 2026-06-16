import asyncio
import base64
import json

import httpx
import pytest

from ecommerce_agent.sandbox.remote import RemoteSandboxClient


def _client(handler, *, token: str = "sekret", session_id: str = "s1") -> RemoteSandboxClient:
    transport = httpx.MockTransport(handler)
    return RemoteSandboxClient(
        base_url="http://executor",
        token=token,
        session_id=session_id,
        timeout_seconds=10,
        transport=transport,
    )


def test_close_is_not_a_coroutine_function() -> None:
    assert asyncio.iscoroutinefunction(RemoteSandboxClient.close) is False


def test_close_issues_delete_session_and_sends_token() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["token"] = request.headers.get("x-sandbox-token")
        return httpx.Response(204)

    client = _client(handler)
    client.close()

    assert seen["method"] == "DELETE"
    assert seen["path"] == "/sessions/s1"
    assert seen["token"] == "sekret"


def test_close_is_idempotent_on_server_no_content() -> None:
    """close() must not raise when the session is already gone (204/no-op)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = _client(handler)
    client.close()
    client.close()


def test_execute_sends_token_and_parses_response() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["token"] = request.headers["x-sandbox-token"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"output": "hi\n", "exit_code": 0, "truncated": False},
        )

    client = _client(handler)
    result = client.execute("echo hi", timeout=5)

    assert captured["method"] == "POST"
    assert captured["path"] == "/sessions/s1/execute"
    assert captured["token"] == "sekret"
    assert captured["body"] == {"command": "echo hi", "timeout": 5}
    assert result.output == "hi\n"
    assert result.exit_code == 0
    assert result.truncated is False


def test_execute_defaults_timeout_to_none_on_wire() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"output": "", "exit_code": 0, "truncated": False})

    client = _client(handler)
    client.execute("ls")

    assert captured["body"] == {"command": "ls", "timeout": None}


def test_upload_files_sends_base64_and_returns_per_file_results() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=[
                {"path": "/workspace/a.txt", "error": None},
                {"path": "/workspace/bad", "error": "permission_denied"},
            ],
        )

    client = _client(handler)
    results = client.upload_files(
        [("/workspace/a.txt", b"hello"), ("/workspace/bad", b"x")]
    )

    files = captured["body"]["files"]
    assert files[0]["path"] == "/workspace/a.txt"
    assert base64.b64decode(files[0]["content_b64"]) == b"hello"
    assert len(results) == 2
    assert results[0].path == "/workspace/a.txt"
    assert results[0].error is None
    assert results[1].path == "/workspace/bad"
    assert results[1].error == "permission_denied"


def test_download_files_round_trip_decodes_base64() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        return httpx.Response(
            200,
            json={
                "path": "/workspace/a.txt",
                "content_b64": base64.b64encode(b"hello").decode("ascii"),
                "error": None,
            },
        )

    client = _client(handler)
    results = client.download_files(["/workspace/a.txt"])

    assert captured["method"] == "GET"
    assert captured["path"] == "/sessions/s1/files/workspace/a.txt"
    assert results[0].path == "/workspace/a.txt"
    assert results[0].content == b"hello"
    assert results[0].error is None


def test_download_files_reports_server_error_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"path": "/workspace/missing", "content_b64": None, "error": "file_not_found"},
        )

    client = _client(handler)
    results = client.download_files(["/workspace/missing"])

    assert results[0].content is None
    assert results[0].error == "file_not_found"


def test_download_path_strips_leading_slash_and_keeps_nested_segments() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={"path": "/workspace/sub/dir/f.csv", "content_b64": "", "error": None},
        )

    client = _client(handler)
    client.download_files(["/workspace/sub/dir/f.csv"])

    assert captured["path"] == "/sessions/s1/files/workspace/sub/dir/f.csv"


def test_download_normalizes_relative_path_to_workspace_for_parity() -> None:
    """DockerSandbox treats relative paths as /workspace-relative; the remote
    client must do the same on the wire so downloads match upload semantics."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={"path": "nested/data.txt", "content_b64": "aGk=", "error": None},
        )

    client = _client(handler)
    results = client.download_files(["nested/data.txt"])

    assert captured["path"] == "/sessions/s1/files/workspace/nested/data.txt"
    assert results[0].path == "nested/data.txt"
    assert results[0].content == b"hi"


def test_download_rejects_invalid_path_locally_without_request() -> None:
    """Client-side normalization gives a defensive invalid_path without a call."""
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200)

    client = _client(handler)
    results = client.download_files(["/etc/passwd"])

    assert called["n"] == 0
    assert results[0].content is None
    assert results[0].error == "invalid_path"


def test_token_sent_on_every_request() -> None:
    """X-Sandbox-Token must be present on execute, upload, download, and delete."""
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers.get("x-sandbox-token", ""))
        if request.url.path == "/sessions/s1/execute":
            return httpx.Response(200, json={"output": "", "exit_code": 0, "truncated": False})
        if request.url.path == "/sessions/s1/files":
            return httpx.Response(200, json=[{"path": "/workspace/a", "error": None}])
        if request.url.path.startswith("/sessions/s1/files/"):
            return httpx.Response(
                200, json={"path": "/workspace/a", "content_b64": "", "error": None}
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    client = _client(handler, token="tok-all")
    client.execute("ls")
    client.upload_files([("/workspace/a", b"x")])
    client.download_files(["/workspace/a"])
    client.close()

    assert all(t == "tok-all" for t in tokens)
    assert len(tokens) == 4


def test_execute_raises_on_server_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.execute("ls")
