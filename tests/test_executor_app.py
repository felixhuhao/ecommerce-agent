import base64
import os
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import sandbox_executor.app as mod
from sandbox_executor.app import (
    _build_bwrap_args,
    _filtered_env,
    _reap_expired,
    _safe_on_disk_path,
    _sandbox_file_path,
    _validate_session_id,
    create_app,
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "TOKEN", "test-tok")
    monkeypatch.setattr(mod, "WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "DEFAULT_TIMEOUT_SECONDS", 30)
    mod.app = create_app()
    return TestClient(mod.app)


# --------------------------------------------------------------------------- #
# Path confinement
# --------------------------------------------------------------------------- #
def test_sandbox_file_path_accepts_workspace_relative() -> None:
    assert _sandbox_file_path("foo.csv") == "/workspace/foo.csv"
    assert _sandbox_file_path("/workspace/sub/a.csv") == "/workspace/sub/a.csv"


def test_sandbox_file_path_accepts_deepagents_edit_tmp() -> None:
    assert _sandbox_file_path("/tmp/.deepagents_edit_abc") == "/tmp/.deepagents_edit_abc"


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "/workspace",
        "/tmp/other",
        "/tmp/../etc/passwd",
        "/workspaces/other/x",
        "",
    ],
)
def test_sandbox_file_path_rejects_traversal(bad: str) -> None:
    with pytest.raises((PermissionError, ValueError)):
        _sandbox_file_path(bad)


# --------------------------------------------------------------------------- #
# Host-side safe path mapping
# --------------------------------------------------------------------------- #
def test_safe_on_disk_path_maps_workspace(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mod, "WORKSPACES_DIR", str(tmp_path))
    workspace = tmp_path / "s1"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()

    assert _safe_on_disk_path("/workspace/a.csv", "s1") == f"{tmp_path}/s1/a.csv"
    assert _safe_on_disk_path("sub/b.csv", "s1") == f"{tmp_path}/s1/sub/b.csv"


def test_safe_on_disk_path_maps_edit_tmp_to_session_tmp(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mod, "WORKSPACES_DIR", str(tmp_path))
    workspace = tmp_path / "s1"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()

    assert (
        _safe_on_disk_path("/tmp/.deepagents_edit_abc", "s1")
        == f"{tmp_path}/s1/.tmp/.deepagents_edit_abc"
    )


# --------------------------------------------------------------------------- #
# Env allowlist
# --------------------------------------------------------------------------- #
def test_filtered_env_excludes_mongo_credentials(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PYTHONPATH", "/opt/ecommerce_analysis")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("MONGO_URL", "mongodb://root:secret@host/admin")
    monkeypatch.setenv("MONGO_INITDB_ROOT_PASSWORD", "pw")

    env = _filtered_env()

    assert env["PATH"] == "/usr/bin"
    assert env["PYTHONPATH"] == "/opt/ecommerce_analysis"
    assert env["HOME"] == "/tmp"
    assert "MONGO_URL" not in env
    assert "MONGO_INITDB_ROOT_PASSWORD" not in env


# --------------------------------------------------------------------------- #
# bwrap layout
# --------------------------------------------------------------------------- #
def test_build_bwrap_args_binds_workspace_and_hides_parent() -> None:
    args = _build_bwrap_args("/workspaces/s1")
    joined = " ".join(args)

    assert "--bind /workspaces/s1 /workspace" in joined
    assert "--bind /workspaces/s1/.tmp /tmp" in joined
    assert "--ro-bind /usr /usr" in joined
    assert "--ro-bind /bin /bin" in joined
    assert "--ro-bind /lib /lib" in joined
    assert "--ro-bind /opt /opt" in joined
    assert "--proc /proc" in joined
    assert "--dev /dev" in joined
    assert "--chdir /workspace" in joined
    # the /workspaces parent itself must NOT be exposed
    assert "--bind /workspaces /workspaces" not in joined
    assert "--ro-bind /workspaces /workspaces" not in joined


# --------------------------------------------------------------------------- #
# Health (no token)
# --------------------------------------------------------------------------- #
def test_health_succeeds_without_token(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# Token gating
# --------------------------------------------------------------------------- #
def test_execute_rejects_missing_token(client) -> None:
    resp = client.post("/sessions/s1/execute", json={"command": "echo hi"})
    assert resp.status_code == 401


def test_execute_rejects_wrong_token(client) -> None:
    resp = client.post(
        "/sessions/s1/execute",
        json={"command": "echo hi"},
        headers={"X-Sandbox-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_all_protected_routes_reject_missing_token(client) -> None:
    cases = [
        ("post", "/sessions/s1/execute", {"command": "x"}),
        ("post", "/sessions/s1/files", {"files": []}),
        ("get", "/sessions/s1/files/workspace/a", None),
        ("delete", "/sessions/s1/files/workspace/a", None),
        ("delete", "/sessions/s1", None),
        ("post", "/maintenance/reap", None),
    ]
    for method, path, body in cases:
        call = getattr(client, method)
        resp = call(path, json=body) if body is not None else call(path)
        assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"


# --------------------------------------------------------------------------- #
# Execute (monkeypatched runner)
# --------------------------------------------------------------------------- #
def test_execute_returns_output_with_correct_token(client, monkeypatch) -> None:
    captured = {}

    def fake_run(command, session_id, timeout):
        captured.update(command=command, session_id=session_id, timeout=timeout)
        return {"output": "ok\n", "exit_code": 0, "truncated": False}

    monkeypatch.setattr(mod, "_run_execute", fake_run)

    resp = client.post(
        "/sessions/s1/execute",
        json={"command": "echo ok", "timeout": 7},
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"output": "ok\n", "exit_code": 0, "truncated": False}
    assert captured == {"command": "echo ok", "session_id": "s1", "timeout": 7}


# --------------------------------------------------------------------------- #
# File round-trip
# --------------------------------------------------------------------------- #
def test_upload_then_download_round_trip(client) -> None:
    headers = {"X-Sandbox-Token": "test-tok"}
    upload = client.post(
        "/sessions/s1/files",
        json={
            "files": [
                {
                    "path": "/workspace/data.csv",
                    "content_b64": base64.b64encode(b"a,b\n1,2\n").decode(),
                }
            ]
        },
        headers=headers,
    )
    assert upload.status_code == 200
    assert upload.json() == [{"path": "/workspace/data.csv", "error": None}]

    dl = client.get("/sessions/s1/files/workspace/data.csv", headers=headers)
    assert dl.status_code == 200
    body = dl.json()
    assert body["error"] is None
    assert base64.b64decode(body["content_b64"]) == b"a,b\n1,2\n"


def test_upload_rejects_traversal_path(client) -> None:
    resp = client.post(
        "/sessions/s1/files",
        json={"files": [{"path": "/etc/passwd", "content_b64": ""}]},
        headers={"X-Sandbox-Token": "test-tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == [{"path": "/etc/passwd", "error": "invalid_path"}]


def test_download_missing_file_reports_error(client) -> None:
    resp = client.get(
        "/sessions/s1/files/workspace/nope.csv",
        headers={"X-Sandbox-Token": "test-tok"},
    )
    assert resp.status_code == 200
    assert resp.json()["error"] == "file_not_found"


def test_delete_file_then_404(client) -> None:
    headers = {"X-Sandbox-Token": "test-tok"}
    client.post(
        "/sessions/s1/files",
        json={"files": [{"path": "/workspace/x", "content_b64": base64.b64encode(b"y").decode()}]},
        headers=headers,
    )

    deleted = client.delete("/sessions/s1/files/workspace/x", headers=headers)
    assert deleted.status_code == 204

    again = client.delete("/sessions/s1/files/workspace/x", headers=headers)
    assert again.status_code == 404


def test_files_persist_within_session(client) -> None:
    headers = {"X-Sandbox-Token": "test-tok"}
    client.post(
        "/sessions/s1/files",
        json={
            "files": [
                {
                    "path": "/workspace/p.csv",
                    "content_b64": base64.b64encode(b"persist").decode(),
                }
            ]
        },
        headers=headers,
    )
    dl = client.get("/sessions/s1/files/workspace/p.csv", headers=headers)
    assert base64.b64decode(dl.json()["content_b64"]) == b"persist"


def test_edit_tmp_path_round_trips(client) -> None:
    headers = {"X-Sandbox-Token": "test-tok"}
    client.post(
        "/sessions/s1/files",
        json={
            "files": [
                {
                    "path": "/tmp/.deepagents_edit_abc",
                    "content_b64": base64.b64encode(b"edit").decode(),
                }
            ]
        },
        headers=headers,
    )
    dl = client.get("/sessions/s1/files/tmp/.deepagents_edit_abc", headers=headers)
    assert dl.status_code == 200
    assert base64.b64decode(dl.json()["content_b64"]) == b"edit"


# --------------------------------------------------------------------------- #
# Session delete (idempotent)
# --------------------------------------------------------------------------- #
def test_delete_session_is_idempotent(client) -> None:
    headers = {"X-Sandbox-Token": "test-tok"}
    client.post(
        "/sessions/s1/files",
        json={"files": [{"path": "/workspace/a", "content_b64": base64.b64encode(b"b").decode()}]},
        headers=headers,
    )

    first = client.delete("/sessions/s1", headers=headers)
    second = client.delete("/sessions/s1", headers=headers)

    assert first.status_code == 204
    assert second.status_code == 204


# --------------------------------------------------------------------------- #
# Reap
# --------------------------------------------------------------------------- #
def test_reap_removes_expired_workspaces(monkeypatch, tmp_path) -> None:
    import os

    monkeypatch.setattr(mod, "WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "SESSION_IDLE_TTL_SECONDS", 100)

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    os.utime(fresh, (time.time(), time.time()))

    stale = tmp_path / "stale"
    stale.mkdir()
    old = time.time() - 600
    os.utime(stale, (old, old))

    removed = _reap_expired()
    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


# --------------------------------------------------------------------------- #
# session_id validation (path-traversal guard)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_id",
        [
            "..",
            ".",
            "",
            "a/b",
            "a\\b",
            "a b",
            "a.b",
            "a..b",
            "a\x00b",
            "a:b",
            "abc\n",
            "x" * 129,
        ],
    )
def test_validate_session_id_rejects_unsafe_values(bad_id: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_session_id(bad_id)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("good_id", ["abc123", "a" * 128, "sess-42_foo", "0123456789abcdef"])
def test_validate_session_id_accepts_safe_values(good_id: str) -> None:
    assert _validate_session_id(good_id) == good_id


def test_delete_session_with_traversal_id_is_blocked_and_victim_survives(
    monkeypatch, tmp_path
) -> None:
    """DELETE /sessions/<traversal> must 400 and NOT touch the workspace parent."""
    monkeypatch.setattr(mod, "TOKEN", "test-tok")
    monkeypatch.setattr(mod, "WORKSPACES_DIR", str(tmp_path))
    mod.app = create_app()
    client = TestClient(mod.app)

    victim = tmp_path / "victim"
    victim.mkdir()
    headers = {"X-Sandbox-Token": "test-tok"}

    resp = client.delete("/sessions/.traversal", headers=headers)
    assert resp.status_code == 400
    assert victim.exists(), "workspace parent must not be deleted"


def test_execute_with_encoded_traversal_session_id_returns_400(client) -> None:
    # %2E%2E decodes to ".." at the router; the validator must block it.
    resp = client.post(
        "/sessions/%2E%2E/execute",
        json={"command": "echo x"},
        headers={"X-Sandbox-Token": "test-tok"},
    )
    assert resp.status_code == 400


def test_valid_session_still_works_after_validator(client) -> None:
    headers = {"X-Sandbox-Token": "test-tok"}
    resp = client.delete("/sessions/abc123", headers=headers)
    assert resp.status_code == 204


# --------------------------------------------------------------------------- #
# Symlink escape guard for host-side file API
# --------------------------------------------------------------------------- #
def test_download_rejects_symlink_escape(client, tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    os.symlink(outside, workspace / "link")

    resp = client.get(
        "/sessions/s1/files/workspace/link",
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["content_b64"] is None
    assert body["error"] == "invalid_path"


def test_upload_rejects_symlink_escape_without_overwriting_target(client, tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    os.symlink(outside, workspace / "link")

    resp = client.post(
        "/sessions/s1/files",
        json={
            "files": [
                {
                    "path": "/workspace/link",
                    "content_b64": base64.b64encode(b"pwn").decode(),
                }
            ]
        },
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 200
    assert resp.json() == [{"path": "/workspace/link", "error": "invalid_path"}]
    assert outside.read_text() == "secret"


def test_delete_rejects_symlink_escape_without_removing_link(client, tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    link = workspace / "link"
    os.symlink(outside, link)

    resp = client.delete(
        "/sessions/s1/files/workspace/link",
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 400
    assert link.is_symlink()
    assert outside.read_text() == "secret"


def test_tmp_root_symlink_escape_is_rejected(client, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".deepagents_edit_abc").write_text("secret")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    tmp_dir = workspace / ".tmp"
    tmp_dir.mkdir()
    tmp_dir.rmdir()
    os.symlink(outside, tmp_dir)

    resp = client.get(
        "/sessions/s1/files/tmp/.deepagents_edit_abc",
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["content_b64"] is None
    assert body["error"] == "invalid_path"


def test_download_rejects_symlinked_intermediate_directory(client, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("secret")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    os.symlink(outside, workspace / "evil")

    resp = client.get(
        "/sessions/s1/files/workspace/evil/file.txt",
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["content_b64"] is None
    assert body["error"] == "invalid_path"


def test_upload_rejects_symlinked_intermediate_directory(client, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "file.txt"
    target.write_text("secret")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    os.symlink(outside, workspace / "evil")

    resp = client.post(
        "/sessions/s1/files",
        json={
            "files": [
                {
                    "path": "/workspace/evil/file.txt",
                    "content_b64": base64.b64encode(b"pwn").decode(),
                }
            ]
        },
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 200
    assert resp.json() == [{"path": "/workspace/evil/file.txt", "error": "invalid_path"}]
    assert target.read_text() == "secret"


def test_tmp_edit_path_rejects_symlinked_intermediate_directory(client, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("secret")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    tmp_root = workspace / ".tmp"
    tmp_root.mkdir()
    os.symlink(outside, tmp_root / ".deepagents_edit_evil")

    resp = client.get(
        "/sessions/s1/files/tmp/.deepagents_edit_evil/file.txt",
        headers={"X-Sandbox-Token": "test-tok"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["content_b64"] is None
    assert body["error"] == "invalid_path"
