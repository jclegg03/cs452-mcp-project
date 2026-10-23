"""Versioned context and structured output contract for SQL generation."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parent


class SQLProposal(BaseModel):
    """Strict response shape, followed by application consistency checks."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["query", "deny", "clarify"]
    sql: str | None
    parameters: list[str | int | float | bool | None]
    message: str | None = Field(max_length=500)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision == "query":
            if not self.sql or not self.sql.strip() or self.message is not None:
                raise ValueError("A query requires SQL and a null message.")
            if self.sql.count("%s") != len(self.parameters):
                raise ValueError("The number of SQL parameters does not match its placeholders.")
        elif self.sql is not None or self.parameters or not self.message or not self.message.strip():
            raise ValueError("A denial or clarification requires a message and no SQL/parameters.")
        return self


@dataclass(frozen=True)
class SQLContext:
    prefix: str
    cache_key: str

    def user_message(self, question: str, as_of: datetime | None = None) -> str:
        reference = as_of if as_of is not None else datetime.now(timezone.utc)
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError("The reference time must include a timezone.")
        reference = reference.astimezone(timezone.utc)
        return json.dumps({
            "available_bind_values": {
                "reference_time_utc": reference.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "last_7_days_start_utc": (reference - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S.%f"),
            },
            "clock_note": "Reference values only. Apply time filters only if requested in the question.",
            "question": question,
        }, ensure_ascii=False)


def build_sql_context(root: Path = PROJECT_ROOT) -> SQLContext:
    instructions = (root / "prompts/sql_generation.txt").read_text().strip()
    schema = json.loads((root / "context/schema.json").read_text())
    rules = (root / "context/domain_rules.yaml").read_text().strip()
    examples = json.loads((root / "context/sql_examples.json").read_text())
    if not schema.get("show_tables") or not schema.get("create_tables"):
        raise ValueError("Schema context is empty; run schema_context.py first.")
    # Canonical JSON avoids cache misses caused by map insertion order.
    prefix = "\n\n".join((
        instructions,
        "DATABASE_SCHEMA:\n" + json.dumps(schema, sort_keys=True, ensure_ascii=False),
        "DOMAIN_RULES:\n" + rules,
        "QUESTION_TO_SQL_EXAMPLES:\n" + json.dumps(examples, sort_keys=True, ensure_ascii=False),
    ))
    digest = hashlib.sha256(prefix.encode()).hexdigest()[:32]
    return SQLContext(prefix=prefix, cache_key=f"survey-sql:{digest}")


@lru_cache(maxsize=1)
def load_sql_context() -> SQLContext:
    """Load once per process; restart or clear this cache after a refresh."""
    return build_sql_context()
