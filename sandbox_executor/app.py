"""Sandbox executor service (design doc §6.2/§6.6).

Exposes a small HTTP API wrapped as a DeepAgents ``BaseSandbox`` via
``RemoteSandboxClient``. Execution is subprocess-per-``execute`` inside a
per-execute bubblewrap mount namespace that binds the session workspace as
``/workspace`` (cross-session isolation), with a process-group timeout kill,
explicit env allowlist, and per-session execute serialization.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import logging
import os
import posixpath
import re
import shutil
import signal
import subprocess
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("sandbox_executor")

_OUTPUT_LIMIT = 64 * 1024
_MAX_UPLOAD_BYTES = 512 * 1024
_TIMEOUT_EXIT = 124
_WORKSPACE_ROOT = "/workspace"
_DEEPAGENTS_EDIT_TMP_PREFIX = "/tmp/.deepagents_edit_"
_ENV_ALLOWLIST = ("PATH", "PYTHONPATH", "LANG")
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

TOKEN = os.environ.get("SANDBOX_EXECUTOR_TOKEN", "")
WORKSPACES_DIR = os.environ.get("WORKSPACES_DIR", "/workspaces")
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("SANDBOX_EXECUTE_TIMEOUT_SECONDS", "30"))
SESSION_IDLE_TTL_SECONDS = int(os.environ.get("SESSION_IDLE_TTL_SECONDS", "1800"))

# Session ids are filesystem path components, so they must be strictly safe:
# no path separators, no "." / "..", no NUL, bounded length. The agent generates
# uuid4().hex; this rejects anything an attacker substitutes via the URL.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


# --------------------------------------------------------------------------- #
# Path confinement (mirrors ``ecommerce_agent.sandbox.backend._sandbox_file_path``
# — the executor image cannot import agent src, so the rule is duplicated).
# --------------------------------------------------------------------------- #
def _sandbox_file_path(path: str) -> str:
    raw_path = path if posixpath.isabs(path) else posixpath.join(_WORKSPACE_ROOT, path)
    normalized = posixpath.normpath(raw_path)
    is_workspace_file = normalized.startswith(f"{_WORKSPACE_ROOT}/")
    is_deepagents_edit_tmp = normalized.startswith(_DEEPAGENTS_EDIT_TMP_PREFIX)
    if normalized == _WORKSPACE_ROOT or not (is_workspace_file or is_deepagents_edit_tmp):
        raise PermissionError(
            f"path must be inside {_WORKSPACE_ROOT} or a DeepAgents edit temp file: {path!r}"
        )
    name = posixpath.basename(normalized)
    if not name:
        raise ValueError(f"invalid file path: {path!r}")
    return normalized


def _workspace_dir(session_id: str) -> str:
    return os.path.join(WORKSPACES_DIR, session_id)


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _path_root_and_target(sandbox_abs_path: str, session_id: str) -> tuple[str, str]:
    """Return the allowed host root and lexical target for a sandbox path."""
    normalized = _sandbox_file_path(sandbox_abs_path)
    workspace = _workspace_dir(session_id)
    if normalized.startswith(f"{_WORKSPACE_ROOT}/"):
        rel = normalized[len(f"{_WORKSPACE_ROOT}/") :]
        return workspace, os.path.join(workspace, rel)
    rel = normalized[len("/tmp/") :]
    tmp_root = os.path.join(workspace, ".tmp")
    return tmp_root, os.path.join(tmp_root, rel)


def _ensure_safe_parent(target: str, root: str) -> None:
    """Create missing parents without following existing symlink directories."""
    root_real = os.path.realpath(root)
    parent = os.path.dirname(target)
    parent_rel = os.path.relpath(parent, root)
    current = root
    if parent_rel != ".":
        for part in parent_rel.split(os.sep):
            current = os.path.join(current, part)
            try:
                os.lstat(current)
            except FileNotFoundError:
                os.mkdir(current)
                continue
            if os.path.islink(current) or not os.path.isdir(current):
                raise PermissionError(f"unsafe parent path: {parent!r}")
    if not _is_within(os.path.realpath(parent), root_real):
        raise PermissionError(f"parent escapes sandbox root: {parent!r}")


def _safe_on_disk_path(
    sandbox_abs_path: str,
    session_id: str,
    *,
    create_parent: bool = False,
) -> str:
    """Resolve a sandbox path for host-side file APIs without symlink escape.

    The executed code may create symlinks inside ``/workspace``. The HTTP file
    API runs outside bwrap, so it must reject any path whose real target leaves
    the session workspace or its mapped ``.tmp`` root before opening/removing it.
    """
    root, target = _path_root_and_target(sandbox_abs_path, session_id)
    workspace_real = os.path.realpath(_workspace_dir(session_id))
    root_real = os.path.realpath(root)
    if not _is_within(root_real, workspace_real):
        raise PermissionError(f"root escapes session workspace: {sandbox_abs_path!r}")
    if create_parent:
        _ensure_safe_parent(target, root)
    if not _is_within(os.path.realpath(target), root_real):
        raise PermissionError(f"path escapes sandbox root: {sandbox_abs_path!r}")
    return target


def _write_file_no_follow(path: str, content: bytes) -> None:
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_NOFOLLOW,
        0o644,
    )
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)


def _read_file_no_follow(path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# Workspace lifecycle
# --------------------------------------------------------------------------- #
def _ensure_workspace(session_id: str) -> str:
    workspace = _workspace_dir(session_id)
    os.makedirs(os.path.join(workspace, ".tmp"), exist_ok=True)
    return workspace


def _delete_workspace(session_id: str) -> None:
    workspace = _workspace_dir(session_id)
    if os.path.isdir(workspace):
        shutil.rmtree(workspace, ignore_errors=True)


def _reap_expired(now: float | None = None) -> int:
    """Remove workspaces idle longer than the TTL. Returns the count removed."""
    if not os.path.isdir(WORKSPACES_DIR):
        return 0
    cutoff = (now if now is not None else time.time()) - SESSION_IDLE_TTL_SECONDS
    removed = 0
    for entry in os.listdir(WORKSPACES_DIR):
        path = os.path.join(WORKSPACES_DIR, entry)
        if not os.path.isdir(path):
            continue
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _filtered_env() -> dict[str, str]:
    """Explicit minimal allowlist; never includes Mongo/app credentials."""
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    # HOME must be writable inside the namespace; the per-session /tmp is bound.
    env["HOME"] = "/tmp"
    return env


def _build_bwrap_args(workspace_dir: str) -> list[str]:
    """Bubblewrap mount-namespace args (probe-derived, design doc §6.6).

    Binds the session workspace as ``/workspace`` and its ``.tmp`` as ``/tmp``,
    mounts runtime/helper-kit paths read-only, and does NOT expose the
    ``/workspaces`` parent (cross-session isolation).
    """
    return [
        "bwrap",
        "--bind", workspace_dir, "/workspace",
        "--bind", os.path.join(workspace_dir, ".tmp"), "/tmp",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/opt", "/opt",
        "--ro-bind", "/etc", "/etc",
        "--proc", "/proc",
        "--dev", "/dev",
        "--chdir", "/workspace",
    ]


def _run_execute(command: str, session_id: str, timeout: int | None) -> dict:
    """Run ``command`` in a per-execute bwrap namespace; kill the full tree on timeout."""
    workspace = _ensure_workspace(session_id)
    seconds = int(timeout or DEFAULT_TIMEOUT_SECONDS)
    bwrap_cmd = _build_bwrap_args(workspace) + ["/bin/sh", "-c", command]
    try:
        proc = subprocess.Popen(  # noqa: S603 - command is the analytical payload
            bwrap_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=_filtered_env(),
        )
    except FileNotFoundError as exc:
        return {"output": f"[bwrap not available: {exc}]", "exit_code": 127, "truncated": False}

    try:
        output_bytes, _ = proc.communicate(timeout=seconds)
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_process_group(proc.pid)
        try:
            output_bytes, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            output_bytes = b""
        exit_code = _TIMEOUT_EXIT
        timed_out = True

    text = output_bytes.decode("utf-8", errors="replace")
    truncated = len(text) > _OUTPUT_LIMIT
    if truncated:
        text = f"{text[:_OUTPUT_LIMIT]}\n[output truncated]"
    if timed_out:
        text += f"\n[execution exceeded {seconds}s timeout]"
    _touch_workspace(session_id)
    return {"output": text, "exit_code": exit_code, "truncated": truncated}


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _touch_workspace(session_id: str) -> None:
    try:
        os.utime(_workspace_dir(session_id), None)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Per-session execute serialization (design doc §6.6)
# --------------------------------------------------------------------------- #
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_locks[session_id] = lock
        return lock


def _drop_session_lock(session_id: str) -> None:
    with _session_locks_guard:
        _session_locks.pop(session_id, None)


# --------------------------------------------------------------------------- #
# Auth + session_id validation
# --------------------------------------------------------------------------- #
async def verify_token(x_sandbox_token: str | None = Header(default=None)) -> None:
    """Constant-time token check on every non-health route (design doc §6.5)."""
    if not TOKEN:
        raise HTTPException(status_code=503, detail="executor token not configured")
    if x_sandbox_token is None or not hmac.compare_digest(x_sandbox_token, TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing token")


def _validate_session_id(session_id: str) -> str:
    """Reject path-traversal / unsafe session ids BEFORE any filesystem use.

    ``session_id`` is joined into a filesystem path (``/workspaces/{session_id}``),
    so a value like ``..`` would escape the workspace root. Run as a route
    dependency so it gates every session-scoped handler.
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    return session_id


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ExecuteRequest(BaseModel):
    command: str
    timeout: int | None = None


class FileItem(BaseModel):
    path: str
    content_b64: str


class UploadRequest(BaseModel):
    files: list[FileItem]


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    os.makedirs(WORKSPACES_DIR, exist_ok=True)
    logger.info("sandbox executor ready (workspaces=%s)", WORKSPACES_DIR)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="sandbox-executor", lifespan=_lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post(
        "/sessions/{session_id}/execute",
        dependencies=[Depends(verify_token), Depends(_validate_session_id)],
    )
    def execute(session_id: str, request: ExecuteRequest) -> dict:
        with _session_lock(session_id):
            return _run_execute(request.command, session_id, request.timeout)

    @app.post(
        "/sessions/{session_id}/files",
        dependencies=[Depends(verify_token), Depends(_validate_session_id)],
    )
    def upload_files(session_id: str, request: UploadRequest) -> list[dict]:
        _ensure_workspace(session_id)
        results: list[dict] = []
        for item in request.files:
            try:
                on_disk = _safe_on_disk_path(
                    item.path,
                    session_id,
                    create_parent=True,
                )
            except (PermissionError, ValueError):
                results.append({"path": item.path, "error": "invalid_path"})
                continue
            try:
                content = base64.b64decode(item.content_b64, validate=True)
            except (binascii.Error, ValueError):
                results.append({"path": item.path, "error": "invalid_path"})
                continue
            if len(content) > _MAX_UPLOAD_BYTES:
                results.append({"path": item.path, "error": "invalid_path"})
                continue
            try:
                _write_file_no_follow(on_disk, content)
                results.append({"path": item.path, "error": None})
            except OSError:
                results.append({"path": item.path, "error": "permission_denied"})
        _touch_workspace(session_id)
        return results

    @app.get(
        "/sessions/{session_id}/files/{file_path:path}",
        dependencies=[Depends(verify_token), Depends(_validate_session_id)],
    )
    def download_file(session_id: str, file_path: str) -> dict:
        sandbox_abs = "/" + file_path
        try:
            on_disk = _safe_on_disk_path(sandbox_abs, session_id)
        except (PermissionError, ValueError):
            return {"path": sandbox_abs, "content_b64": None, "error": "invalid_path"}
        if not os.path.isfile(on_disk):
            return {"path": sandbox_abs, "content_b64": None, "error": "file_not_found"}
        try:
            content = _read_file_no_follow(on_disk)
        except OSError:
            return {"path": sandbox_abs, "content_b64": None, "error": "permission_denied"}
        return {
            "path": sandbox_abs,
            "content_b64": base64.b64encode(content).decode("ascii"),
            "error": None,
        }

    @app.delete(
        "/sessions/{session_id}/files/{file_path:path}",
        dependencies=[Depends(verify_token), Depends(_validate_session_id)],
    )
    def delete_file(session_id: str, file_path: str):
        sandbox_abs = "/" + file_path
        try:
            on_disk = _safe_on_disk_path(sandbox_abs, session_id)
        except (PermissionError, ValueError):
            raise HTTPException(status_code=400, detail="invalid_path") from None
        if not os.path.exists(on_disk):
            raise HTTPException(status_code=404, detail="file_not_found")
        try:
            os.remove(on_disk)
        except IsADirectoryError:
            raise HTTPException(status_code=400, detail="is_directory") from None
        except OSError:
            raise HTTPException(status_code=403, detail="permission_denied") from None
        return JSONResponse(status_code=204, content=None)

    @app.delete(
        "/sessions/{session_id}",
        dependencies=[Depends(verify_token), Depends(_validate_session_id)],
    )
    def delete_session(session_id: str):
        _delete_workspace(session_id)
        _drop_session_lock(session_id)
        return JSONResponse(status_code=204, content=None)

    @app.post("/maintenance/reap", dependencies=[Depends(verify_token)])
    def reap() -> dict:
        removed = _reap_expired()
        return {"removed": removed}

    return app


app = create_app()
