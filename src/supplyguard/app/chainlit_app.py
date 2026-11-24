"""Chainlit entry point: run with `chainlit run src/supplyguard/app/chainlit_app.py`."""

import chainlit as cl

from supplyguard.auth import get_registry


@cl.data_layer
def no_data_layer():
    # Chainlit would otherwise treat DATABASE_URL as its own persistence layer.
    return None


@cl.password_auth_callback
async def authenticate(username: str, password: str) -> cl.User | None:
    context = get_registry().authenticate(username, password)
    if context is None:
        return None
    return cl.User(identifier=context.user_id, metadata={"role": context.role})


@cl.on_chat_start
async def start() -> None:
    user = cl.user_session.get("user")
    registry = get_registry()
    # Scopes are re-derived from configuration, never taken from the session token.
    context = registry.authorization_for(user.identifier)
    if context is None:
        await cl.Message("Your account has no access configuration.").send()
        return
    cl.user_session.set("authorization", context)
    await cl.Message(
        f"Signed in as {registry.display_name(context.user_id)} ({context.role}). "
        "Case investigation is not connected yet."
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    await cl.Message("The investigation workflow is not connected yet.").send()
