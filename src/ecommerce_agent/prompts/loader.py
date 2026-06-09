from __future__ import annotations

import functools
from pathlib import Path

import yaml

_PROMPTS_PATH = Path(__file__).parent / "prompts.yml"


@functools.lru_cache(maxsize=8)
def load_prompts(path: str | None = None) -> dict[str, str]:
    target = Path(path) if path else _PROMPTS_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"prompts file {target} must be a mapping of name -> prompt")

    prompts: dict[str, str] = {}
    for name, prompt in data.items():
        if not isinstance(name, str) or not isinstance(prompt, str):
            raise ValueError(f"prompts file {target} must map string names to string prompts")
        prompts[name] = prompt
    return prompts


def get_prompt(name: str, path: str | None = None) -> str:
    prompts = load_prompts(path)
    if name not in prompts:
        raise KeyError(f"prompt {name!r} not found in {path or _PROMPTS_PATH}")
    return prompts[name]
