import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from openai import OpenAIError
from ModelConnector import ClarificationRequiredError, SQLQuery

from main import main
from user_input import MAX_INPUT_CHARS


class MainTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DATABASE_URL": "mysql://test@localhost/db"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.dotenv = patch("main.load_dotenv")
        self.load_dotenv = self.dotenv.start()
        self.addCleanup(self.dotenv.stop)
        database_patch = patch("main.DatabaseConnector")
        self.database = database_patch.start()
        self.addCleanup(database_patch.stop)
        cursor = self.database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("test_db", "test_version", 1)

    @patch("main.create_model_connector")
    def test_no_prompt_only_checks_database(self, factory):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)
        factory.assert_not_called()

    @patch("main.create_model_connector")
    def test_prompt_prints_model_response(self, factory):
        connector = factory.return_value.__enter__.return_value
        connector.generate_response.return_value = "Hello!"
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--prompt", "Say hello"]), 0)
        connector.generate_response.assert_called_once_with("Say hello")
        factory.return_value.__exit__.assert_called_once()
        self.assertIn("Hello!", output.getvalue())

    @patch("main.create_model_connector")
    def test_model_request_error_returns_failure(self, factory):
        connector = factory.return_value.__enter__.return_value
        connector.generate_response.side_effect = OpenAIError("request failed")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--prompt", "Say hello"]), 1)
        self.assertIn("Model request failed", output.getvalue())
        factory.return_value.__exit__.assert_called_once()

    @patch("main.create_model_connector")
    def test_invalid_prompt_skips_credentials_database_and_model(self, factory):
        for prompt in (" ", "x" * (MAX_INPUT_CHARS + 1), "question\x1b",
                       "Count users; SELECT * FROM users"):
            with self.subTest(length=len(prompt)):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["--prompt", prompt]), 1)
                self.assertIn("Invalid input:", output.getvalue())
        self.load_dotenv.assert_not_called()
        self.database.assert_not_called()
        factory.assert_not_called()

    @patch("main.create_model_connector")
    @patch("builtins.input", return_value="  How many orders?  ")
    def test_interactive_input_is_normalized(self, read_input, factory):
        connector = factory.return_value.__enter__.return_value
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--interactive"]), 0)
        read_input.assert_called_once()
        connector.generate_response.assert_called_once_with("How many orders?")

    @patch("main.create_model_connector")
    @patch("builtins.input", return_value="  ")
    def test_interactive_input_uses_same_validation(self, read_input, factory):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--interactive"]), 1)
        self.database.assert_not_called()
        factory.assert_not_called()

    @patch("main.create_model_connector")
    def test_interactive_eof_or_interrupt_does_no_external_work(self, factory):
        for error, code in ((EOFError, 0), (KeyboardInterrupt, 130)):
            with self.subTest(error=error.__name__):
                with patch("builtins.input", side_effect=error):
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(main(["--interactive"]), code)
        self.load_dotenv.assert_not_called()
        self.database.assert_not_called()
        factory.assert_not_called()

    @patch("main.create_model_connector")
    def test_generation_only_does_not_open_database_or_execute_sql(self, factory):
        connector = factory.return_value.__enter__.return_value
        connector.generate_sql.return_value = SQLQuery("SELECT COUNT(*) FROM Survey")
        connector.last_usage = None
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--generate-sql", "--prompt", "How many surveys?"]), 0)
        self.assertIn('"executed": false', output.getvalue())
        self.database.assert_not_called()
        connector.execute_sql.assert_not_called()

    @patch("main.create_model_connector")
    @patch("builtins.input", side_effect=AssertionError("Scripted mode must not read input"))
    def test_generation_clarification_is_a_normal_outcome(self, read_input, factory):
        connector = factory.return_value.__enter__.return_value
        connector.generate_sql.side_effect = ClarificationRequiredError("Which team?")
        connector.last_usage = None
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--generate-sql", "--prompt", "How many athletes?"]), 0)
        self.assertIn('"decision": "clarify"', output.getvalue())
        self.assertIn("Which team?", output.getvalue())
        self.database.assert_not_called()

    @patch("main.create_model_connector")
    @patch("builtins.input", side_effect=["How many surveys?", "Only active athletes."])
    def test_interactive_clarification_resumes_instead_of_starting_a_new_request(self, read_input, factory):
        connector = factory.return_value.__enter__.return_value
        connector.last_usage = None
        connector.generate_sql.side_effect = [ClarificationRequiredError("Which population?"), SQLQuery("SELECT COUNT(*) FROM Survey")]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--generate-sql", "--interactive"]), 0)
        first, second = connector.generate_sql.call_args_list
        self.assertEqual(first.args[0], second.args[0])
        self.assertEqual(first.kwargs["as_of"], second.kwargs["as_of"])
        self.assertEqual(second.kwargs["clarifications"][0].answer, "Only active athletes.")
        self.assertIn('"decision": "query"', output.getvalue())
        self.assertIn('"requests": 2', output.getvalue())
        self.database.assert_not_called()
        connector.execute_sql.assert_not_called()

    @patch("main.create_model_connector")
    @patch("builtins.input", side_effect=["How many surveys?", " ", "SELECT * FROM Survey", "Active athletes."])
    def test_invalid_clarification_is_reprompted_without_spending_an_api_call(self, read_input, factory):
        connector = factory.return_value.__enter__.return_value
        connector.last_usage = None
        connector.generate_sql.side_effect = [ClarificationRequiredError("Which population?"), SQLQuery("SELECT 1")]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--generate-sql", "--interactive"]), 0)
        self.assertEqual(connector.generate_sql.call_count, 2)
        self.assertEqual(read_input.call_count, 4)
        self.assertEqual(output.getvalue().count("Invalid input:"), 2)

    @patch("main.create_model_connector")
    def test_clarification_can_be_cancelled_without_another_model_call(self, factory):
        connector = factory.return_value.__enter__.return_value
        connector.last_usage = None
        for cancellation, expected in (("/cancel", 0), (EOFError, 0), (KeyboardInterrupt, 130)):
            with self.subTest(cancellation=cancellation):
                connector.generate_sql.reset_mock()
                connector.generate_sql.side_effect = ClarificationRequiredError("Which population?")
                output = io.StringIO()
                with patch("builtins.input", side_effect=["How many surveys?", cancellation]):
                    with redirect_stdout(output):
                        self.assertEqual(main(["--generate-sql", "--interactive"]), expected)
                connector.generate_sql.assert_called_once()
                self.assertIn('"decision": "cancelled"', output.getvalue())
        self.database.assert_not_called()

    @patch("main.create_model_connector")
    @patch("builtins.input", side_effect=["How many surveys?", " ", " ", " "])
    def test_invalid_reply_retry_limit(self, read_input, factory):
        connector = factory.return_value.__enter__.return_value
        connector.last_usage = None
        connector.generate_sql.side_effect = ClarificationRequiredError("Which population?")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--generate-sql", "--interactive"]), 1)
        connector.generate_sql.assert_called_once()

    @patch("main.create_model_connector")
    @patch("builtins.input", side_effect=["How many surveys?", "Active athletes.", "TEAM-X", "All time."])
    def test_clarification_round_limit_stops_the_cli(self, read_input, factory):
        connector = factory.return_value.__enter__.return_value
        connector.last_usage = None
        connector.generate_sql.side_effect = ClarificationRequiredError("Please be more specific.")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--generate-sql", "--interactive"]), 1)
        self.assertEqual(connector.generate_sql.call_count, 4)
        self.assertIn('"decision": "clarification_limit"', output.getvalue())

    @patch("main.create_model_connector")
    def test_generation_validates_input_before_client_or_database(self, factory):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--generate-sql", "--prompt", "SELECT * FROM Survey"]), 1)
        self.database.assert_not_called()
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
