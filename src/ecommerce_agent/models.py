from langchain_openai import ChatOpenAI

from ecommerce_agent.config import Settings, get_settings


def get_primary_model(settings: Settings | None = None) -> ChatOpenAI:
    settings = settings or get_settings()
    if not settings.llm_api_key:
        raise ValueError("LLM_API_KEY is required to build the primary model")

    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        streaming=True,
    )


def get_summary_model(settings: Settings | None = None) -> ChatOpenAI:
    # Week 3: summary model can diverge from the primary model.
    return get_primary_model(settings)


def get_fallback_model(settings: Settings | None = None) -> ChatOpenAI:
    # Week 3: fallback provider/model can be configured separately.
    return get_primary_model(settings)
