from ecommerce_agent.config import Settings
from ecommerce_agent.evals.metadata import prompt_hash, run_metadata


def test_prompt_hash_is_named_and_stable() -> None:
    h1 = prompt_hash("sales_analyst")
    h2 = prompt_hash("sales_analyst")
    h3 = prompt_hash("router_classifier")

    assert h1 == h2 and len(h1) == 16
    assert h1 != h3


def test_run_metadata_uses_model_override_when_given() -> None:
    settings = Settings(_env_file=None, llm_api_key="k", llm_model="primary")
    model = {"name": "deepseek-v4-flash", "temperature": 0.0}

    md = run_metadata(settings, prompt_name="router_classifier", model=model)

    assert md["model"] == model
    assert set(md) == {"git_commit", "prompt_hash", "dependency_versions", "model"}


def test_run_metadata_defaults_to_primary_model() -> None:
    settings = Settings(_env_file=None, llm_api_key="k", llm_model="primary")

    md = run_metadata(settings, prompt_name="sales_analyst")

    assert md["model"]["name"] == "primary"
