"""Select the configured model provider after loading .env."""

import os

from ModelConnector import ModelConnector
from OpenAIModelConnector import DEFAULT_MODEL, OpenAIModelConnector


def create_model_connector() -> ModelConnector:
    provider = os.getenv("MODEL_PROVIDER", "").strip().lower()
    if provider != "openai":
        raise ValueError("Unsupported MODEL_PROVIDER. Supported value: openai.")

    return OpenAIModelConnector(
        api_key=os.getenv("MODEL_API_KEY", ""),
        model=os.getenv("MODEL_NAME", DEFAULT_MODEL),
    )
