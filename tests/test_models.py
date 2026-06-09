from ecommerce_agent.config import Settings
from ecommerce_agent.models import get_primary_model


def test_primary_model_uses_configured_low_temperature() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key", llm_temperature=0.1)

    model = get_primary_model(settings)

    assert model.temperature == 0.1
