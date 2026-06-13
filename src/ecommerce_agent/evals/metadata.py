from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess

from ecommerce_agent.config import Settings
from ecommerce_agent.prompts.loader import get_prompt


def git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in ("deepagents", "langgraph", "langchain-mcp-adapters", "langchain-openai"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def prompt_hash(name: str) -> str:
    return hashlib.sha256(get_prompt(name).encode("utf-8")).hexdigest()[:16]


def run_metadata(
    settings: Settings,
    *,
    prompt_name: str,
    model: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "git_commit": git_commit(),
        "prompt_hash": prompt_hash(prompt_name),
        "dependency_versions": dependency_versions(),
        "model": model
        or {
            "name": settings.llm_model,
            "base_url": settings.llm_base_url,
            "temperature": settings.llm_temperature,
        },
    }
