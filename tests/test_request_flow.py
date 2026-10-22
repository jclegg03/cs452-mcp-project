import unittest
from unittest.mock import patch

from ModelConnector import (
    ClarificationRequiredError,
    ModelConnector,
    QueryResult,
    RequestDeniedError,
)
from user_input import InputValidationError


class ExampleConnector(ModelConnector):
    """A deterministic provider to test the workflow without services."""

    def __init__(self):
        super().__init__("test-key")
        self.result = QueryResult(rows=[{"count": 3}])
        self.sql_validated = False

    def _generate_response(self, prompt):
        return prompt

    def generate_sql(self, question, context):
        return "SELECT COUNT(*) AS count FROM example"

    def validate_sql(self, sql):
        self.sql_validated = True

    def execute_sql(self, sql):
        if not self.sql_validated:
            raise AssertionError("SQL executed before validation")
        return self.result

    def prepare_results(self, result):
        return result

    def generate_answer(self, question, result):
        return f"Found {result.rows[0]['count']} records."


class RequestFlowTests(unittest.TestCase):
    def test_invalid_input_never_reaches_sql_generation(self):
        connector = ExampleConnector()
        with patch.object(connector, "generate_sql") as generate:
            with self.assertRaises(InputValidationError):
                connector.process_request(" ")
        generate.assert_not_called()

    def test_validated_question_and_context_reach_generation(self):
        connector = ExampleConnector()
        with patch.object(connector, "generate_sql", wraps=connector.generate_sql) as generate:
            self.assertEqual(connector.process_request(" Count records? ", "schema"), "Found 3 records.")
        generate.assert_called_once_with("Count records?", "schema")

    def test_denial_and_clarification_skip_execution(self):
        for error in (RequestDeniedError("Database questions only."),
                      ClarificationRequiredError("Which date range?")):
            with self.subTest(error=type(error).__name__):
                connector = ExampleConnector()
                with patch.object(connector, "generate_sql", side_effect=error):
                    with patch.object(connector, "execute_sql") as execute:
                        self.assertEqual(connector.process_request("question"), str(error))
                execute.assert_not_called()

    def test_sql_validation_failure_never_reaches_execution(self):
        connector = ExampleConnector()
        with patch.object(connector, "validate_sql", side_effect=ValueError("Invalid SQL")):
            with patch.object(connector, "execute_sql") as execute:
                with self.assertRaisesRegex(ValueError, "Invalid SQL"):
                    connector.process_request("question")
        execute.assert_not_called()

    def test_empty_results_skip_preparation_and_answer_call(self):
        connector = ExampleConnector()
        connector.result = QueryResult(rows=[])
        with patch.object(connector, "prepare_results") as prepare:
            with patch.object(connector, "generate_answer") as answer:
                self.assertEqual(connector.process_request("question"), "No matching data found.")
        prepare.assert_not_called()
        answer.assert_not_called()

    def test_count_zero_is_data_rather_than_empty_results(self):
        connector = ExampleConnector()
        connector.result = QueryResult(rows=[{"count": 0}])
        self.assertEqual(connector.process_request("Count records?"), "Found 0 records.")

    def test_execution_failure_skips_answer_and_is_not_retried(self):
        connector = ExampleConnector()
        with patch.object(connector, "execute_sql", side_effect=RuntimeError("Timed out")) as execute:
            with patch.object(connector, "generate_answer") as answer:
                with self.assertRaisesRegex(RuntimeError, "Timed out"):
                    connector.process_request("question")
        execute.assert_called_once()
        answer.assert_not_called()

    def test_prepared_results_and_truncation_metadata_reach_answer(self):
        connector = ExampleConnector()
        prepared = QueryResult(rows=[{"count": 2}], truncated=True)
        with patch.object(connector, "prepare_results", return_value=prepared):
            with patch.object(connector, "generate_answer", return_value="Limited result") as answer:
                self.assertEqual(connector.process_request("question"), "Limited result")
        answer.assert_called_once_with("question", prepared)


if __name__ == "__main__":
    unittest.main()
