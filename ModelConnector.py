"""Common interface for model providers."""

from abc import ABC, abstractmethod


class ModelConnector(ABC):
    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise ValueError("Set MODEL_API_KEY to your provider's API key.")
        self.api_key = api_key.strip()

    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Generate a text response to the supplied prompt."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by the connector."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
