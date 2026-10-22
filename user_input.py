"""Local input checks shared by the CLI and model connectors."""

import re
import unicodedata
from urllib.parse import unquote


MAX_INPUT_CHARS = 2000

# Match SQL-shaped phrases, rather than banning individual words or punctuation.
_IDENTIFIER = r"(?:`[^`\r\n]+`|[a-z_]\w*)(?:\s*\.\s*(?:`[^`\r\n]+`|[a-z_]\w*))*"
_EXPRESSION = rf"(?:\*|{_IDENTIFIER}(?:\s*\.\s*\*)?|[a-z_]\w*\s*\([^;()]*\))"
_COLUMN = rf"{_EXPRESSION}(?:\s+as\s+{_IDENTIFIER})?"
_SQL_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"```\s*(?:sql|mysql)\b",
    rf"\bselect\s+(?:(?:distinct|all)\s+)?{_COLUMN}(?:\s*,\s*{_COLUMN})*\s+from\s+{_IDENTIFIER}",
    r"\bselect\s+(?:[+-]?\d+\b|['\"]|@@?\w+|[a-z_]\w*\s*\()",
    r"\bunion\s+(?:(?:all|distinct)\s+)?select\b",
    rf"\b(?:insert|replace)\s+(?:ignore\s+)?into\s+{_IDENTIFIER}",
    rf"\bupdate\s+{_IDENTIFIER}\s+set\s+{_IDENTIFIER}\s*=",
    rf"\bdelete\s+from\s+{_IDENTIFIER}",
    r"\bshow\s+(?:databases|schemas|tables|processlist|grants)\s*(?:;|$)",
    rf"\bshow\s+(?:(?:full\s+)?columns\s+from|create\s+table)\s+{_IDENTIFIER}",
    rf"\b(?:describe|desc)\s+{_IDENTIFIER}\s*;",
    rf"\bcall\s+{_IDENTIFIER}\s*\(",
    rf"\btruncate\s+(?:table\s+)?{_IDENTIFIER}\s*(?:;|$)",
    r"\b(?:create|alter|drop|truncate|rename)\s+(?:temporary\s+)?"
    r"(?:table|database|schema|view|index|user|procedure|function|trigger|event)\b",
    r"\b(?:or|and|where|having)\s+(?:\d+\s*=\s*\d+|['\"][^'\"\r\n]*['\"]\s*=\s*['\"][^'\"\r\n]*['\"])",
    r"\b(?:or|and)\s+(?:true|false)\s*(?:--|\#|/\*|;|$)",
    r"\b(?:sleep|benchmark|load_file)\s*\(",
    r"\binto\s+(?:outfile|dumpfile)\b",
))
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_EXECUTABLE_COMMENT = re.compile(r"/\*!(?:\d{5,6})?\s*(.*?)\*/", re.DOTALL)


def _contains_sql(value: str) -> bool:
    """Recognize common SQL snippets, including simple obfuscation.

    Normalize a detection-only copy. Never rewrite suspicious input into a
    supposedly safe prompt or treat this heuristic as SQL execution validation.
    """
    candidate = value
    # Two decoding passes cover plain and doubly percent-encoded snippets.
    for _ in range(3):
        candidate = unicodedata.normalize("NFKC", candidate)
        candidate = "".join(
            char for char in candidate if unicodedata.category(char) != "Cf"
        )
        candidate = _EXECUTABLE_COMMENT.sub(r" \1 ", candidate)
        variants = (
            candidate,
            _BLOCK_COMMENT.sub(" ", candidate),
            _BLOCK_COMMENT.sub("", candidate),
        )
        if any(pattern.search(text) for text in variants for pattern in _SQL_PATTERNS):
            return True
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return False


class InputValidationError(ValueError):
    """The request cannot proceed because the input is invalid."""


def validate_user_input(value: object) -> str:
    """Validate untrusted text and trim only its surrounding whitespace.

    This bounds the user-input size, not the exact model token count, and
    rejects recognizable SQL snippets. It does not determine database relevance
    or detect arbitrary prompt injection.
    """
    if not isinstance(value, str):
        raise InputValidationError("The prompt must be text.")
    # Check before stripping so whitespace padding cannot bypass the limit.
    if len(value) > MAX_INPUT_CHARS:
        raise InputValidationError(
            f"The prompt must be {MAX_INPUT_CHARS} characters or fewer."
        )
    if not value.strip():
        raise InputValidationError("The prompt must not be empty.")
    if any(
        unicodedata.category(char) in {"Cc", "Cs"} and char not in "\t\r\n"
        for char in value
    ):
        raise InputValidationError("The prompt contains unsupported control characters.")
    if _contains_sql(value):
        raise InputValidationError(
            "SQL statements or injection patterns are not allowed in user input. "
            "Describe your database question in plain English instead."
        )
    return value.strip()
