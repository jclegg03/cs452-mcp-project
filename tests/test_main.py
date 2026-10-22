import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from openai import OpenAIError

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
        for prompt in (" ", "x" * (MAX_INPUT_CHARS + 1), "question\x1b"):
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


if __name__ == "__main__":
    unittest.main()
