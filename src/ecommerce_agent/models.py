from langchain_openai import ChatOpenAI

from ecommerce_agent.config import Settings, get_settings

CLASSIFIER_TEMPERATURE = 0.0
CLASSIFIER_MAX_TOKENS = 256
CLASSIFIER_TIMEOUT_SECONDS = 20
CLASSIFIER_STRUCTURED_OUTPUT_METHOD = "function_calling"


def get_primary_model(settings: Settings | None = None) -> ChatOpenAI:
    settings = settings or get_settings()
    if not settings.llm_api_key:
        raise ValueError("LLM_API_KEY is required to build the primary model")

    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        streaming=True,
    )


def get_summary_model(settings: Settings | None = None) -> ChatOpenAI:
    # Week 3: summary model can diverge from the primary model.
    return get_primary_model(settings)


def get_fallback_model(settings: Settings | None = None) -> ChatOpenAI:
    # Week 3: fallback provider/model can be configured separately.
    return get_primary_model(settings)


def get_classifier_model(settings: Settings | None = None) -> ChatOpenAI:
    settings = settings or get_settings()
    if not settings.llm_api_key:
        raise ValueError("LLM_API_KEY is required to build the classifier model")

    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=CLASSIFIER_TEMPERATURE,
        max_tokens=CLASSIFIER_MAX_TOKENS,
        timeout=CLASSIFIER_TIMEOUT_SECONDS,
        streaming=False,
        extra_body={"thinking": {"type": "disabled"}},
    )


def classifier_model_params(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    return {
        "name": settings.llm_model,
        "base_url": settings.llm_base_url,
        "temperature": CLASSIFIER_TEMPERATURE,
        "max_tokens": CLASSIFIER_MAX_TOKENS,
        "streaming": False,
        "timeout_seconds": CLASSIFIER_TIMEOUT_SECONDS,
        "thinking": "disabled",
        "structured_output_method": CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    }
