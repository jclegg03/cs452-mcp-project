"""Common interface for model providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from user_input import validate_user_input

if TYPE_CHECKING:
    from clarification import ClarificationTurn
    from sql_context import SQLContext


class RequestDeniedError(ValueError):
    """Query generation or validation rejected the request."""


class ClarificationRequiredError(ValueError):
    """The user must clarify the request before a query can run."""


@dataclass
class QueryResult:
    """Named database values and whether the result was cut short."""

    rows: list[dict[str, object]]
    truncated: bool = False
    columns: list[str] = field(default_factory=list)
    sql: str | None = None
    parameters: tuple = ()
    question_context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SQLQuery:
    """A proposed statement and separately bound values; not yet validated."""

    sql: str
    parameters: tuple[str | int | float | bool | None, ...] = ()


class ModelConnector(ABC):
    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise ValueError("Set MODEL_API_KEY to your provider's API key.")
        self.api_key = api_key.strip()
        self.last_usage = None

    def generate_response(self, prompt: str) -> str:
        """Provider smoke test: validate input before making a model request."""
        return self._generate_response(validate_user_input(prompt))

    def process_request(
        self, user_input: str, context: "SQLContext | None" = None, *,
        as_of: datetime | None = None, clarifications: tuple["ClarificationTurn", ...] = (),
    ) -> str:
        """Template method for the database-question workflow.

        Subclasses implement the individual stages, not their ordering. Clarification
        exceptions propagate so a caller can collect a reply and resume with
        history; no SQL executes while a clarification is pending.
        """
        question = validate_user_input(user_input)
        try:
            sql = self.generate_sql(question, context, as_of=as_of, clarifications=clarifications)
            return self.respond_to_query(question, sql, as_of=as_of, clarifications=clarifications)
        except RequestDeniedError as exc:
            return str(exc)

    def respond_to_query(self, question: str, sql: SQLQuery, *, as_of=None,
                         clarifications=(), observe=None) -> str:
        """Finish an already-proposed query without making another SQL model call."""
        from clarification import validate_clarifications

        question = validate_user_input(question)
        validate_clarifications(question, clarifications)
        emit = observe if observe is not None else lambda event, **data: None
        self.validate_sql(sql)
        result = self.execute_sql(sql, observe=emit)
        result.question_context = {
            "reference_time": as_of.isoformat() if as_of is not None else None,
            "clarifications": [{"question": turn.question, "answer": turn.answer}
                               for turn in clarifications],
        }
        emit("sql_results", columns=result.columns, rows=result.rows,
             truncated=result.truncated, executed_sql=result.sql, parameters=result.parameters)
        if not result.rows:
            answer = "No matching data found."
            emit("natural_language", text=answer, source="application", model_called=False)
            return answer
        prepared_result = self.prepare_results(result)
        emit("model_answer_request", question=question, context=result.question_context)
        answer = self.generate_answer(question, prepared_result)
        usage = self.last_usage.model_dump() if self.last_usage is not None else None
        emit("natural_language", text=answer, source="model", usage=usage)
        return answer

    @abstractmethod
    def _generate_response(self, prompt: str) -> str:
        """Make the provider-specific text request after local validation."""
        raise NotImplementedError

    @abstractmethod
    def generate_sql(
        self, question: str, context: "SQLContext | None" = None, *,
        as_of: datetime | None = None, clarifications: tuple["ClarificationTurn", ...] = (),
    ) -> SQLQuery:
        """Propose SQL, or raise a denial or clarification exception."""
        raise NotImplementedError

    @abstractmethod
    def validate_sql(self, sql: SQLQuery) -> None:
        """Enforce query rules before any SQL execution."""
        raise NotImplementedError

    @abstractmethod
    def execute_sql(self, sql: SQLQuery, *, observe=None) -> QueryResult:
        """Delegate execution to the database with resource limits."""
        raise NotImplementedError

    @abstractmethod
    def prepare_results(self, result: QueryResult) -> QueryResult:
        """Bound and serialize results for the answer-generation call."""
        raise NotImplementedError

    @abstractmethod
    def generate_answer(self, question: str, result: QueryResult) -> str:
        """Explain the returned data in plain English."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by the connector."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
