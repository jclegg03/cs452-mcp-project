"""Common interface for model providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from user_input import validate_user_input

if TYPE_CHECKING:
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

    def generate_response(self, prompt: str) -> str:
        """Provider smoke test: validate input before making a model request."""
        return self._generate_response(validate_user_input(prompt))

    def process_request(self, user_input: str, context: "SQLContext | None" = None) -> str:
        """Template method for the database-question workflow.

        Subclasses implement the individual stages, not their ordering. SQL
        Validation, execution and explanation are future milestones. Failures
        propagate to the caller; no query is retried automatically.
        """
        question = validate_user_input(user_input)
        try:
            sql = self.generate_sql(question, context)
            self.validate_sql(sql)
        except (RequestDeniedError, ClarificationRequiredError) as exc:
            return str(exc)

        result = self.execute_sql(sql)
        if not result.rows:
            return "No matching data found."
        prepared_result = self.prepare_results(result)
        return self.generate_answer(question, prepared_result)

    @abstractmethod
    def _generate_response(self, prompt: str) -> str:
        """Make the provider-specific text request after local validation."""
        raise NotImplementedError

    @abstractmethod
    def generate_sql(self, question: str, context: "SQLContext | None" = None) -> SQLQuery:
        """Propose SQL, or raise a denial or clarification exception."""
        raise NotImplementedError

    @abstractmethod
    def validate_sql(self, sql: SQLQuery) -> None:
        """Enforce query rules before any SQL execution."""
        raise NotImplementedError

    @abstractmethod
    def execute_sql(self, sql: SQLQuery) -> QueryResult:
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
