"""OpenAI implementation of the model provider interface."""

from datetime import datetime

from openai import OpenAI

from ModelConnector import (
    ClarificationRequiredError, ModelConnector, QueryResult, RequestDeniedError, SQLQuery,
)
from sql_context import SQLContext, SQLProposal, load_sql_context
from user_input import validate_user_input


DEFAULT_MODEL = "gpt-5-mini"


class OpenAIModelConnector(ModelConnector):
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        super().__init__(api_key)
        if not model or not model.strip():
            raise ValueError("MODEL_NAME must be a non-empty OpenAI model ID.")
        self.model = model.strip()
        self._client = OpenAI(api_key=self.api_key, timeout=60.0)
        self.last_usage = None

    def _generate_response(self, prompt: str) -> str:
        response = self._client.responses.create(model=self.model, input=prompt)
        if not response.output_text:
            raise RuntimeError("OpenAI returned a response without text output.")
        return response.output_text

    def generate_sql(
        self, question: str, context: SQLContext | None = None, *, as_of: datetime | None = None,
    ) -> SQLQuery:
        question = validate_user_input(question)
        self.last_usage = None
        context = context if context is not None else load_sql_context()
        if not isinstance(context, SQLContext):
            raise ValueError("SQL context must be a loaded SQLContext, not user text.")
        response = self._client.responses.parse(
            model=self.model,
            instructions=context.prefix,
            input=[{"role": "user", "content": context.user_message(question, as_of)}],
            text_format=SQLProposal,
            prompt_cache_key=context.cache_key,
            max_output_tokens=3000,
        )
        self.last_usage = response.usage
        if response.status != "completed":
            raise RuntimeError("SQL generation was incomplete; no query may be executed.")
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "refusal":
                        raise RequestDeniedError("The model declined this request.")
        proposal = response.output_parsed
        if not isinstance(proposal, SQLProposal):
            raise RuntimeError("The model did not return a structured SQL decision.")
        if proposal.decision == "deny":
            raise RequestDeniedError(proposal.message)
        if proposal.decision == "clarify":
            raise ClarificationRequiredError(proposal.message)
        return SQLQuery(sql=proposal.sql, parameters=tuple(proposal.parameters))

    # Execution remains stopped until its independent validator is implemented.
    def validate_sql(self, sql: SQLQuery) -> None:
        raise NotImplementedError("SQL validation is not implemented yet.")

    def execute_sql(self, sql: SQLQuery) -> QueryResult:
        raise NotImplementedError("SQL execution is not implemented yet.")

    def prepare_results(self, result: QueryResult) -> QueryResult:
        raise NotImplementedError("Result preparation is not implemented yet.")

    def generate_answer(self, question: str, result: QueryResult) -> str:
        raise NotImplementedError("Database answer generation is not implemented yet.")

    def close(self) -> None:
        self._client.close()
