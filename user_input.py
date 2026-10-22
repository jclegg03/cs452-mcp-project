"""Local input checks shared by the CLI and model connectors."""

import unicodedata


MAX_INPUT_CHARS = 2000


class InputValidationError(ValueError):
    """The request cannot proceed because the input is invalid."""


def validate_user_input(value: object) -> str:
    """Validate untrusted text and trim only its surrounding whitespace.

    This bounds the user-input size, not the exact model token count. It does
    not determine database relevance or detect prompt injection.
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
    return value.strip()
