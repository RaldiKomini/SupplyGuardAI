"""The only place a chat model is created. Other code receives a LangChain BaseChatModel."""

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel


def make_model() -> BaseChatModel:
    """Model chosen by MODEL_PROVIDER and MODEL_NAME, so it changes without code changes.

    For Azure OpenAI, MODEL_NAME is the deployment and OPENAI_API_VERSION must be set.
    """
    provider = os.environ["MODEL_PROVIDER"]
    name = os.environ["MODEL_NAME"]
    options: dict = {"temperature": 0}
    if provider == "azure_openai":
        options["azure_deployment"] = name
        if base_url := os.getenv("MODEL_BASE_URL"):
            options["azure_endpoint"] = base_url
    elif base_url := os.getenv("MODEL_BASE_URL"):
        options["base_url"] = base_url
    if api_key := os.getenv("MODEL_API_KEY"):
        options["api_key"] = api_key
    return init_chat_model(name, model_provider=provider, **options)
