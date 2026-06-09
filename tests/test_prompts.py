from pathlib import Path

import pytest

from ecommerce_agent.prompts.loader import get_prompt, load_prompts


def test_get_sales_analyst_prompt_is_nonempty_and_read_only() -> None:
    prompt = get_prompt("sales_analyst")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "read-only" in prompt.lower()
    assert "ecommerce_analysis" in prompt
    assert "generate_visualization" in prompt


def test_get_prompt_unknown_key_raises() -> None:
    with pytest.raises(KeyError, match="not found"):
        get_prompt("does_not_exist")


def test_load_prompts_rejects_non_mapping(tmp_path: Path) -> None:
    prompts_path = tmp_path / "prompts.yml"
    prompts_path.write_text("- nope\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a mapping"):
        load_prompts(str(prompts_path))
