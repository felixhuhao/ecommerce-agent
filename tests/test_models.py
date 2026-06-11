from ecommerce_agent.config import Settings
from ecommerce_agent.models import (
    CLASSIFIER_MAX_TOKENS,
    CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    CLASSIFIER_TEMPERATURE,
    CLASSIFIER_TIMEOUT_SECONDS,
    classifier_model_params,
    get_classifier_model,
    get_primary_model,
)


def test_primary_model_uses_configured_low_temperature() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key", llm_temperature=0.1)

    model = get_primary_model(settings)

    assert model.temperature == 0.1


def test_get_classifier_model_is_tuned_for_classification() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
    )

    model = get_classifier_model(settings)

    assert model.model_name == "deepseek-v4-flash"
    assert model.temperature == CLASSIFIER_TEMPERATURE == 0.0
    assert model.max_tokens == CLASSIFIER_MAX_TOKENS
    assert model.streaming is False
    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_classifier_model_params_records_actual_params() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
    )

    params = classifier_model_params(settings)

    assert params == {
        "name": "deepseek-v4-flash",
        "base_url": settings.llm_base_url,
        "temperature": CLASSIFIER_TEMPERATURE,
        "max_tokens": CLASSIFIER_MAX_TOKENS,
        "streaming": False,
        "timeout_seconds": CLASSIFIER_TIMEOUT_SECONDS,
        "thinking": "disabled",
        "structured_output_method": CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    }
    assert CLASSIFIER_STRUCTURED_OUTPUT_METHOD == "function_calling"
