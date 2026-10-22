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

    def test_rejects_sql_even_when_embedded_in_prose(self):
        for question in (
            "SELECT * FROM users;",
            "Please run select email, name FROM users for me.",
            "Use SELECT u.email AS email FROM app.users u to answer.",
            "Please use SELECT COUNT(*) FROM `users`.",
            "SELECT 1;",
            "SELECT @@version",
            "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent;",
            "```sql\nSELECT * FROM users\n```",
            "Here is SQL: ```mysql\nSHOW TABLES;\n```",
            "INSERT INTO users (name) VALUES ('test');",
            "REPLACE INTO users VALUES (1);",
            "UPDATE users SET name = 'test';",
            "DELETE FROM users;",
            "How many users?; DROP TABLE users;",
            "CREATE TABLE example (id INT);",
            "SHOW TABLES;",
            "SHOW DATABASES",
            "SHOW COLUMNS FROM users",
            "SHOW CREATE TABLE users",
            "DESCRIBE users;",
            "CALL refresh_data();",
            "TRUNCATE users;",
        ):
            with self.subTest(question=question):
                with self.assertRaisesRegex(InputValidationError, "SQL"):
                    validate_user_input(question)

    def test_rejects_common_injection_fragments(self):
        for question in (
            "admin' OR 1=1 -- ",
            "admin' oR 'a' = 'a' #",
            "Find orders UNION ALL SELECT password FROM users",
            "' OR TRUE -- ",
            "Please use SLEEP(10)",
            "BENCHMARK(1000000, MD5('x'))",
            "LOAD_FILE('/etc/passwd')",
            "Write results INTO OUTFILE '/tmp/results'",
        ):
            with self.subTest(question=question):
                with self.assertRaisesRegex(InputValidationError, "SQL"):
                    validate_user_input(question)

    def test_rejects_obfuscated_sql(self):
        for question in (
            "sElEcT\n*\tFrOm users",
            "SELECT/**/email/**/FROM/**/users",
            "SEL/**/ECT * FR/**/OM users",
            "/*!50000 SELECT * FROM users */",
            "ＳＥＬＥＣＴ * ＦＲＯＭ users",
            "SEL\u200bECT * FROM users",
            "%53%45%4c%45%43%54%20*%20FROM%20users",
            "%2553%2545%254c%2545%2543%2554%2520*%2520FROM%2520users",
        ):
            with self.subTest(question=question):
                with self.assertRaisesRegex(InputValidationError, "SQL"):
                    validate_user_input(question)

    def test_allows_database_questions_and_isolated_sql_words(self):
        for question in (
            "How many users signed up last month?",
            "Show Alice's orders; include pending ones.",
            "Which union members have orders?",
            "Show the latest update for each customer.",
            "What does the select permission allow?",
            "Find products containing the word DROP.",
            "Find comments containing /* TODO */ or #urgent.",
            "Show orders with a 20% discount.",
            "How many orders cost > $20?",
            "Describe the customer data we have.",
        ):
            with self.subTest(question=question):
                self.assertEqual(validate_user_input(question), question)


if __name__ == "__main__":
    unittest.main()
