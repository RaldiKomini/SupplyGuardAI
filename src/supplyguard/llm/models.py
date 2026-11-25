"""The only place a chat model is created. Other code receives a LangChain BaseChatModel."""

import os
import ssl
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel


def make_model(
    *,
    http_client: httpx.Client | None = None,
    http_async_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
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
        if http_client is not None:
            options["http_client"] = http_client
        if http_async_client is not None:
            options["http_async_client"] = http_async_client
    elif base_url := os.getenv("MODEL_BASE_URL"):
        options["base_url"] = base_url
    if api_key := os.getenv("MODEL_API_KEY"):
        options["api_key"] = api_key
    return init_chat_model(name, model_provider=provider, **options)


@asynccontextmanager
async def open_model() -> AsyncIterator[BaseChatModel]:
    """Keep the Windows Azure compatibility clients open for the workflow's lifetime."""
    if sys.platform != "win32" or os.environ["MODEL_PROVIDER"] != "azure_openai":
        yield make_model()
        return

    # The demo Azure endpoint dropped the default Windows handshake. TLS 1.2 succeeded.
    context = ssl.create_default_context()
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    timeout = httpx.Timeout(60.0, connect=8.0)
    with httpx.Client(verify=context, timeout=timeout) as sync_client:
        async with httpx.AsyncClient(verify=context, timeout=timeout) as async_client:
            yield make_model(http_client=sync_client, http_async_client=async_client)
