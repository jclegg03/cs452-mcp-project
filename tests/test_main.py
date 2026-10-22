import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from openai import OpenAIError

from main import main


class MainTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DATABASE_URL": "mysql://test@localhost/db"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.dotenv = patch("main.load_dotenv")
        self.dotenv.start()
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


if __name__ == "__main__":
    unittest.main()
