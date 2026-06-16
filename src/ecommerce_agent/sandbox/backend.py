"""DockerSandbox: a self-hosted DeepAgents BaseSandbox backend."""

from __future__ import annotations

import base64
import binascii
import posixpath
import shlex
import threading
import time
import uuid

import docker
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from docker.errors import APIError, NotFound

from ecommerce_agent.sandbox.config import (
    SANDBOX_NAME_PREFIX,
    SandboxLimits,
    container_run_kwargs,
)

_OUTPUT_LIMIT = 64 * 1024
_MAX_UPLOAD_BYTES = 512 * 1024
_TIMEOUT_EXIT = 124
_WORKSPACE_ROOT = "/workspace"
_DEEPAGENTS_EDIT_TMP_PREFIX = "/tmp/.deepagents_edit_"


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


def _file_access_error(exc: Exception) -> str:
    if isinstance(exc, NotFound):
        return "file_not_found"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    return "invalid_path"


def _download_error(output: str) -> str:
    if "Permission denied" in output:
        return "permission_denied"
    if "Is a directory" in output:
        return "is_directory"
    if "No such file" in output or "cannot open" in output:
        return "file_not_found"
    return "invalid_path"


class DockerSandbox(BaseSandbox):
    def __init__(self, limits: SandboxLimits, *, session_id: str | None = None, client=None):
        self._limits = limits
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._client = client or docker.from_env()
        self._container = None
        self._container_lock = threading.RLock()
        self._last_used = time.monotonic()

    @property
    def id(self) -> str:
        return f"{SANDBOX_NAME_PREFIX}{self._session_id}"

    def _ensure_container(self):
        with self._container_lock:
            if self._container is not None:
                try:
                    self._container.reload()
                    if self._container.status == "running":
                        return self._container
                except (APIError, NotFound):
                    self._container = None

            self._container = self._client.containers.run(
                **container_run_kwargs(self._limits, name=self.id)
            )
            return self._container

    def _remove_quietly(self) -> None:
        if self._container is None:
            return
        with self._container_lock:
            if self._container is None:
                return
            try:
                self._container.remove(force=True)
            except NotFound:
                pass
            finally:
                self._container = None

    def close(self) -> None:
        self._remove_quietly()

    def __enter__(self) -> DockerSandbox:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.close()

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        container = self._ensure_container()
        self._last_used = time.monotonic()
        seconds = int(timeout or self._limits.execute_timeout_seconds)
        result = container.exec_run(
            cmd=["timeout", str(seconds), "sh", "-c", command],
            demux=False,
        )
        text = (result.output or b"").decode("utf-8", errors="replace")
        truncated = len(text) > _OUTPUT_LIMIT
        if truncated:
            text = f"{text[:_OUTPUT_LIMIT]}\n[output truncated]"
        if result.exit_code == _TIMEOUT_EXIT:
            text += f"\n[execution exceeded {seconds}s timeout]"
        return ExecuteResponse(output=text, exit_code=result.exit_code, truncated=truncated)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        container = self._ensure_container()
        self._last_used = time.monotonic()
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                if len(content) > _MAX_UPLOAD_BYTES:
                    responses.append(FileUploadResponse(path=path, error="invalid_path"))
                    continue
                target_path = _sandbox_file_path(path)
                directory = posixpath.dirname(target_path)
                encoded = base64.b64encode(content).decode("ascii")
                script = (
                    f"mkdir -p {shlex.quote(directory)} && "
                    f"base64 -d > {shlex.quote(target_path)} <<'__ECOMMERCE_SANDBOX_FILE__'\n"
                    f"{encoded}\n"
                    "__ECOMMERCE_SANDBOX_FILE__"
                )
                result = container.exec_run(cmd=["sh", "-c", script], demux=False)
                if result.exit_code == 0:
                    responses.append(FileUploadResponse(path=path, error=None))
                    continue
                output = (result.output or b"").decode("utf-8", errors="replace")
                if "Permission denied" in output:
                    error = "permission_denied"
                else:
                    error = "invalid_path"
                responses.append(FileUploadResponse(path=path, error=error))
            except Exception as exc:
                responses.append(FileUploadResponse(path=path, error=_file_access_error(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        container = self._ensure_container()
        self._last_used = time.monotonic()
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                target_path = _sandbox_file_path(path)
            except Exception as exc:
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error=_file_access_error(exc),
                    )
                )
                continue

            result = container.exec_run(
                cmd=["sh", "-c", f"base64 -w 0 {shlex.quote(target_path)}"],
                demux=False,
            )
            output = result.output or b""
            if result.exit_code != 0:
                error_text = output.decode("utf-8", errors="replace")
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error=_download_error(error_text),
                    )
                )
                continue

            try:
                content = base64.b64decode(output, validate=True)
            except binascii.Error:
                content = None
                error = "invalid_path"
            else:
                error = None

            responses.append(
                FileDownloadResponse(
                    path=path,
                    content=content,
                    error=error,
                )
            )
        return responses
