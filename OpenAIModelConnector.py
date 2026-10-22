"""OpenAI implementation of the model provider interface."""

from openai import OpenAI

from ModelConnector import ModelConnector, QueryResult


DEFAULT_MODEL = "gpt-5-mini"


class OpenAIModelConnector(ModelConnector):
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        super().__init__(api_key)
        if not model or not model.strip():
            raise ValueError("MODEL_NAME must be a non-empty OpenAI model ID.")
        self.model = model.strip()
        self._client = OpenAI(api_key=self.api_key, timeout=60.0)

    def _generate_response(self, prompt: str) -> str:
        response = self._client.responses.create(model=self.model, input=prompt)
        if not response.output_text:
            raise RuntimeError("OpenAI returned a response without text output.")
        return response.output_text

    # These hooks intentionally stop the workflow until their milestone is
    # implemented. A plain text response must never be executed as SQL.
    def generate_sql(self, question: str, context: str) -> str:
        raise NotImplementedError("SQL generation is not implemented yet.")

    def validate_sql(self, sql: str) -> None:
        raise NotImplementedError("SQL validation is not implemented yet.")

    def execute_sql(self, sql: str) -> QueryResult:
        raise NotImplementedError("SQL execution is not implemented yet.")

    def prepare_results(self, result: QueryResult) -> QueryResult:
        raise NotImplementedError("Result preparation is not implemented yet.")

    def generate_answer(self, question: str, result: QueryResult) -> str:
        raise NotImplementedError("Database answer generation is not implemented yet.")

    def close(self) -> None:
        self._client.close()
