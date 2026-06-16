"""RemoteSandboxClient: a synchronous BaseSandbox over HTTP (sandbox executor).

Translates DeepAgents ``BaseSandbox`` calls into the sandbox-executor wire
contract (design doc §6.2). Fully synchronous because ``SessionRuntime.close()``
is sync and runs ``sandbox.close()`` inside ``asyncio.to_thread`` (§6.2).
"""

from __future__ import annotations

import base64
import binascii
import urllib.parse

import httpx
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from ecommerce_agent.sandbox.backend import _sandbox_file_path


def _encode_file_path(path: str) -> str:
    """Strip the leading ``/`` and percent-encode each segment for the catch-all URL.

    Slashes between segments are preserved (the ``{path:path}`` router captures
    them); only reserved bytes *within* a segment are encoded so URL
    normalization cannot collapse or rewrite the path.
    """
    stripped = path.lstrip("/")
    segments = stripped.split("/")
    return "/".join(urllib.parse.quote(segment, safe="") for segment in segments)


class RemoteSandboxClient(BaseSandbox):
    """Synchronous ``BaseSandbox`` backed by the sandbox executor service."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        session_id: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._session_id = session_id
        self._closed = False
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Sandbox-Token": token},
            timeout=timeout_seconds,
            transport=transport,
        )

    @property
    def id(self) -> str:
        return f"remote-{self._session_id}"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        response = self._client.post(
            f"/sessions/{self._session_id}/execute",
            json={"command": command, "timeout": timeout},
        )
        response.raise_for_status()
        data = response.json()
        return ExecuteResponse(
            output=data["output"],
            exit_code=data.get("exit_code"),
            truncated=data.get("truncated", False),
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        payload = {
            "files": [
                {
                    "path": path,
                    "content_b64": base64.b64encode(content).decode("ascii"),
                }
                for path, content in files
            ]
        }
        response = self._client.post(f"/sessions/{self._session_id}/files", json=payload)
        response.raise_for_status()
        items = response.json()
        return [
            FileUploadResponse(path=item["path"], error=item.get("error"))
            for item in items
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results: list[FileDownloadResponse] = []
        for path in paths:
            # Normalize relative -> sandbox-absolute (/workspace/...) so the wire
            # path matches DockerSandbox semantics (parity with upload). Also a
            # client-side confinement check (defense-in-depth, §6.2).
            try:
                normalized = _sandbox_file_path(path)
            except (PermissionError, ValueError):
                results.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue
            encoded = _encode_file_path(normalized)
            response = self._client.get(
                f"/sessions/{self._session_id}/files/{encoded}"
            )
            response.raise_for_status()
            data = response.json()
            content_b64 = data.get("content_b64")
            content: bytes | None
            if content_b64 is None:
                content = None
            else:
                try:
                    content = base64.b64decode(content_b64, validate=True)
                except (binascii.Error, ValueError):
                    content = None
            results.append(
                FileDownloadResponse(path=path, content=content, error=data.get("error"))
            )
        return results

    def close(self) -> None:
        """Issue ``DELETE /sessions/{id}`` (idempotent) and release the HTTP client.

        Best-effort: a transport error or server failure must not propagate, since
        the registry calls ``close()`` inside ``asyncio.gather`` during eviction and
        stale workspaces are reaped by TTL as a backstop.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._client.delete(f"/sessions/{self._session_id}")
        except httpx.HTTPError:
            pass
        finally:
            self._client.close()
