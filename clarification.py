"""Bounded, request-local clarification state shared by model providers."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ModelConnector import ClarificationRequiredError
from user_input import InputValidationError, validate_user_input

if TYPE_CHECKING:
    from ModelConnector import ModelConnector, SQLQuery
    from sql_context import SQLContext


MAX_CLARIFICATION_ROUNDS = 3
MAX_CLARIFICATION_QUESTION_CHARS = 500
MAX_CONVERSATION_CHARS = 8000


class ClarificationLimitError(RuntimeError):
    """The request needs more clarification than its budget permits."""


@dataclass(frozen=True)
class ClarificationTurn:
    question: str
    answer: str


def validate_clarifications(question: str, turns: tuple[ClarificationTurn, ...]) -> None:
    """Validate every user answer, including calls made outside the CLI."""
    if not isinstance(turns, (tuple, list)) or len(turns) > MAX_CLARIFICATION_ROUNDS:
        raise InputValidationError("Clarification history exceeds the allowed rounds.")
    size = len(question)
    for turn in turns:
        if not isinstance(turn, ClarificationTurn):
            raise InputValidationError("Invalid clarification history.")
        if (not isinstance(turn.question, str) or not turn.question.strip()
                or len(turn.question) > MAX_CLARIFICATION_QUESTION_CHARS):
            raise InputValidationError("The clarification question is missing or too long.")
        validate_user_input(turn.answer)
        size += len(turn.question) + len(turn.answer)
    if size > MAX_CONVERSATION_CHARS:
        raise InputValidationError("Clarification history is too long; shorten your reply.")


class SQLClarificationSession:
    """Generate a proposal, collect an answer if needed, then resume that request.

    No database queries run here. This object is kept in memory for one request
    and never changes shared rules or persists answers to disk.
    """

    def __init__(self, connector: "ModelConnector", question: str,
                 context: "SQLContext | None" = None, *, as_of: datetime | None = None):
        self.question = validate_user_input(question)
        self.connector = connector
        self.context = context
        self.as_of = as_of if as_of is not None else datetime.now(timezone.utc)
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("The reference time must include a timezone.")
        self.turns: tuple[ClarificationTurn, ...] = ()
        self.pending_question: str | None = None
        self.finished = False
        self.request_count = 0
        self.usage: list[dict] = []

    def propose(self) -> "SQLQuery":
        if self.finished:
            raise RuntimeError("This request has already ended; start a new request.")
        if self.pending_question is not None:
            raise RuntimeError("Answer the pending clarification before continuing.")
        self.request_count += 1
        try:
            query = self.connector.generate_sql(
                self.question, self.context, as_of=self.as_of, clarifications=self.turns,
            )
        except ClarificationRequiredError as exc:
            if len(self.turns) >= MAX_CLARIFICATION_ROUNDS:
                self.finished = True
                raise ClarificationLimitError(
                    "The request still needs clarification after three replies. "
                    "Please start again with a more specific question."
                ) from exc
            message = str(exc).strip()
            if not message or len(message) > MAX_CLARIFICATION_QUESTION_CHARS:
                self.finished = True
                raise RuntimeError("The model returned an invalid clarification question.") from exc
            self.pending_question = message
            raise
        except Exception:
            self.finished = True
            raise
        else:
            self.finished = True
            return query
        finally:
            if self.connector.last_usage is not None:
                self.usage.append(self.connector.last_usage.model_dump())

    def reply(self, answer: str) -> None:
        if self.finished or self.pending_question is None:
            raise RuntimeError("There is no pending clarification to answer.")
        answer = validate_user_input(answer)
        turns = self.turns + (ClarificationTurn(self.pending_question, answer),)
        validate_clarifications(self.question, turns)
        self.turns = turns
        self.pending_question = None

    def cancel(self) -> None:
        self.finished = True
        self.pending_question = None

    def usage_totals(self) -> dict:
        return {
            "requests": self.request_count,
            "input_tokens": sum(item.get("input_tokens", 0) for item in self.usage),
            "cached_input_tokens": sum(item.get("input_tokens_details", {}).get("cached_tokens", 0)
                                       for item in self.usage),
            "output_tokens": sum(item.get("output_tokens", 0) for item in self.usage),
        }
