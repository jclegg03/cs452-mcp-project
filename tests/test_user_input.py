import unittest

from user_input import MAX_INPUT_CHARS, InputValidationError, validate_user_input


class UserInputTests(unittest.TestCase):
    def test_rejects_non_text(self):
        for value in (None, 42, b"question", ["question"], {"text": "question"}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(InputValidationError, "text"):
                    validate_user_input(value)

    def test_rejects_empty_and_unicode_whitespace(self):
        for value in ("", " \t\n\r", "\u2003\u00a0"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(InputValidationError, "empty"):
                    validate_user_input(value)

    def test_preserves_question_contents_and_trims_edges(self):
        question = "Which café's orders cost > $20?\nInclude 'pending';\tthanks! ☕"
        self.assertEqual(validate_user_input(f" \t{question}\r\n"), question)

    def test_accepts_exact_character_limit(self):
        question = "界" * MAX_INPUT_CHARS
        self.assertEqual(validate_user_input(question), question)

    def test_rejects_over_limit_instead_of_truncating(self):
        for question in (
            "a" * (MAX_INPUT_CHARS + 1),
            " " * MAX_INPUT_CHARS + "question",
        ):
            with self.subTest(question_length=len(question)):
                with self.assertRaisesRegex(InputValidationError, str(MAX_INPUT_CHARS)):
                    validate_user_input(question)

    def test_rejects_terminal_controls_and_unpaired_surrogates(self):
        for char in ("\x00", "\x1b", "\x08", "\x7f", "\x85", "\ud800"):
            with self.subTest(char=repr(char)):
                with self.assertRaisesRegex(InputValidationError, "control characters"):
                    validate_user_input(f"question{char}")

    def test_does_not_claim_to_detect_instructions_by_keyword(self):
        question = 'Find comments containing "ignore previous instructions".'
        self.assertEqual(validate_user_input(question), question)


if __name__ == "__main__":
    unittest.main()
