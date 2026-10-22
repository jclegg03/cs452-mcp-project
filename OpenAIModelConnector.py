"""OpenAI implementation of the model provider interface."""

from openai import OpenAI

from ModelConnector import ModelConnector


DEFAULT_MODEL = "gpt-5-mini"


class OpenAIModelConnector(ModelConnector):
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        super().__init__(api_key)
        if not model or not model.strip():
            raise ValueError("MODEL_NAME must be a non-empty OpenAI model ID.")
        self.model = model.strip()
        self._client = OpenAI(api_key=self.api_key, timeout=60.0)

    def generate_response(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            raise ValueError("The prompt must not be empty.")
        response = self._client.responses.create(model=self.model, input=prompt)
        if not response.output_text:
            raise RuntimeError("OpenAI returned a response without text output.")
        return response.output_text

    def close(self) -> None:
        self._client.close()
