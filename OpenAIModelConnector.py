"""OpenAI implementation of the model provider interface."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

from openai import OpenAI

from ModelConnector import (
    ClarificationRequiredError, ModelConnector, QueryResult, RequestDeniedError, SQLQuery,
)
from clarification import ClarificationTurn, validate_clarifications
from sql_context import SQLContext, SQLProposal, load_sql_context
from sql_execution import bounded_query, execute_query
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
        clarifications: tuple[ClarificationTurn, ...] = (),
    ) -> SQLQuery:
        question = validate_user_input(question)
        validate_clarifications(question, clarifications)
        self.last_usage = None
        context = context if context is not None else load_sql_context()
        if not isinstance(context, SQLContext):
            raise ValueError("SQL context must be a loaded SQLContext, not user text.")
        messages = [{"role": "user", "content": context.user_message(question, as_of)}]
        for turn in clarifications:
            messages.extend((
                {"role": "assistant", "content": json.dumps({
                    "decision": "clarify", "sql": None, "parameters": [], "message": turn.question,
                })},
                {"role": "user", "content": turn.answer},
            ))
        response = self._client.responses.parse(
            model=self.model,
            instructions=context.prefix,
            input=messages,
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

    def validate_sql(self, sql: SQLQuery) -> None:
        bounded_query(sql)

    def execute_sql(self, sql: SQLQuery, *, observe=None) -> QueryResult:
        url = os.getenv("DATABASE_URL")
        if not url:
            raise ValueError("Set DATABASE_URL in .env before executing SQL.")
        return execute_query(sql, url, observe=observe)

    def prepare_results(self, result: QueryResult) -> QueryResult:
        # The executor already converts values and enforces row/byte budgets.
        return result

    def generate_answer(self, question: str, result: QueryResult) -> str:
        instructions = (Path(__file__).with_name("prompts") / "answer_generation.txt").read_text()
        self.last_usage = None
        response = self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=json.dumps({
                "question": question, **result.question_context,
                "executed_sql": result.sql, "parameters": result.parameters,
                "columns": result.columns, "rows": result.rows,
                "possibly_incomplete": result.truncated,
            }, ensure_ascii=True, allow_nan=False),
            prompt_cache_key="survey-answer:" + hashlib.sha256(instructions.encode()).hexdigest()[:32],
            max_output_tokens=2500,
        )
        self.last_usage = response.usage
        if response.status != "completed":
            raise RuntimeError("Answer generation was incomplete; the SQL results are preserved in the log.")
        for item in response.output:
            if item.type == "message" and any(content.type == "refusal" for content in item.content):
                raise RequestDeniedError("The model declined to explain these query results.")
        if not response.output_text or not response.output_text.strip():
            raise RuntimeError("OpenAI returned an answer without text output.")
        return response.output_text.strip()

    def close(self) -> None:
        self._client.close()
