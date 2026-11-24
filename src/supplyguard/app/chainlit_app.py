"""Chainlit entry point: run with `chainlit run src/supplyguard/app/chainlit_app.py`."""

import chainlit as cl


@cl.data_layer
def no_data_layer():
    # Chainlit would otherwise treat DATABASE_URL as its own persistence layer.
    return None


@cl.on_chat_start
async def start() -> None:
    await cl.Message(
        "SupplyGuard AI is starting up. Case investigation is not connected yet."
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    await cl.Message("The investigation workflow is not connected yet.").send()
