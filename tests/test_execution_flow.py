import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from mysql.connector import Error
from openai import OpenAIError

from experiment_log import ExperimentLog
from main import main
from OpenAIModelConnector import OpenAIModelConnector
from sql_context import SQLProposal


def proposal(decision="query", sql="SELECT COUNT(*) AS total FROM Survey", message=None):
    return SimpleNamespace(status="completed", output=[], usage=None,
                           output_parsed=SQLProposal(decision=decision, sql=sql, parameters=[], message=message))


class ExecutionFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for name, arguments in (
            ("main.load_dotenv", {}),
            ("main.ExperimentLog", {"side_effect": lambda: ExperimentLog(self.directory)}),
            ("OpenAIModelConnector.OpenAI", {}),
            ("sql_execution.DatabaseConnector", {}),
            ("main.create_model_connector", {}),
        ):
            patcher = patch(name, **arguments)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            setattr(self, name.split(".")[-1], mock)
        environment = patch.dict(os.environ, {"DATABASE_URL": "mysql://mcp:secret@localhost/syp_dev"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.connector = OpenAIModelConnector("never-log-this-key")
        self.create_model_connector.return_value = self.connector
        self.client = self.OpenAI.return_value
        self.client.responses.parse.return_value = proposal()
        self.client.responses.create.return_value = SimpleNamespace(
            status="completed", output=[], usage=None, output_text="There are 9 surveys.")
        database = self.DatabaseConnector.return_value
        database.database_name = "syp_dev"
        self.cursor = database.connect.return_value.cursor.return_value
        self.cursor.column_names = ("total",)
        self.cursor.fetchone.side_effect = [(9,), None]

    def run_cli(self, *arguments):
        self.output = io.StringIO()
        with redirect_stdout(self.output):
            status = main(list(arguments))
        paths = list(self.directory.glob("*.jsonl"))
        self.assertEqual(len(paths), 1)
        self.records = [json.loads(line) for line in paths[0].read_text().splitlines()]
        self.events = [record["event"] for record in self.records]
        return status

    def test_end_to_end_transcript_is_ordered_durable_and_contains_no_credentials(self):
        self.assertEqual(self.run_cli("--execute", "--prompt", "How many surveys in total?"), 0)
        self.assertEqual(self.events, ["user_input", "model_sql_request", "model_sql", "sql_execution",
                                       "sql_results", "model_answer_request", "natural_language", "outcome"])
        self.assertEqual(self.records[3]["sql"], "SELECT COUNT(*) AS total FROM Survey LIMIT 101")
        self.assertEqual(self.records[4]["rows"], [{"total": 9}])
        self.assertEqual(self.records[6]["text"], "There are 9 surveys.")
        self.assertTrue(self.records[-1]["executed"])
        self.assertEqual([r["sequence"] for r in self.records], list(range(1, 9)))
        transcript = json.dumps(self.records) + self.output.getvalue()
        for credential in ("never-log-this-key", "mysql://", "secret"):
            self.assertNotIn(credential, transcript)
        self.client.responses.parse.assert_called_once()
        self.client.responses.create.assert_called_once()

    @patch("builtins.input", side_effect=["How many surveys?", "All athletes."])
    def test_clarification_loop_is_logged_and_then_resumed_once(self, read_input):
        self.client.responses.parse.side_effect = [
            proposal("clarify", sql=None, message="Which population?"), proposal(),
        ]
        self.assertEqual(self.run_cli("--execute", "--interactive"), 0)
        self.assertEqual(self.events[:6], ["user_input", "model_sql_request", "model_clarification",
                                          "user_reply", "model_sql_request", "model_sql"])
        self.assertEqual(self.client.responses.parse.call_count, 2)
        answer_request = self.client.responses.create.call_args.kwargs
        self.assertEqual(json.loads(answer_request["input"])["clarifications"],
                         [{"question": "Which population?", "answer": "All athletes."}])
        self.assertEqual(self.events.count("sql_execution"), 1)

    def test_zero_rows_skips_answer_model_and_logs_application_message(self):
        self.cursor.fetchone.side_effect = [None]
        self.assertEqual(self.run_cli("--execute", "--prompt", "List survey IDs."), 0)
        self.client.responses.create.assert_not_called()
        self.assertNotIn("model_answer_request", self.events)
        answer = next(r for r in self.records if r["event"] == "natural_language")
        self.assertEqual(answer["text"], "No matching data found.")
        self.assertFalse(answer["model_called"])

    def test_count_zero_still_calls_answer_model(self):
        self.cursor.fetchone.side_effect = [(0,), None]
        self.assertEqual(self.run_cli("--execute", "--prompt", "How many surveys?"), 0)
        self.client.responses.create.assert_called_once()
        result = next(r for r in self.records if r["event"] == "sql_results")
        self.assertEqual(result["rows"], [{"total": 0}])

    def test_denial_or_clarification_never_opens_database(self):
        self.client.responses.parse.return_value = proposal("clarify", sql=None, message="Which population?")
        self.assertEqual(self.run_cli("--execute", "--prompt", "How many athletes?"), 0)
        self.assertEqual(self.records[-1]["decision"], "clarify")
        self.assertFalse(self.records[-1]["executed"])
        self.DatabaseConnector.assert_not_called()
        self.client.responses.create.assert_not_called()

    def test_unsafe_sql_fails_closed_and_is_preserved_for_experiments(self):
        self.client.responses.parse.return_value = proposal(sql="DELETE FROM Survey")
        self.assertEqual(self.run_cli("--execute", "--prompt", "How many surveys?"), 1)
        self.assertIn("model_sql", self.events)
        self.assertEqual(self.records[-1]["error_type"], "RequestDeniedError")
        self.DatabaseConnector.assert_not_called()
        self.client.responses.create.assert_not_called()

    def test_model_denial_is_logged_without_execution(self):
        self.client.responses.parse.return_value = proposal("deny", sql=None, message="Ask about the survey database.")
        self.assertEqual(self.run_cli("--execute", "--prompt", "Write a poem."), 0)
        self.assertIn("model_denial", self.events)
        self.assertEqual(self.records[-1]["decision"], "deny")
        self.assertFalse(self.records[-1]["executed"])
        self.DatabaseConnector.assert_not_called()

    @patch("builtins.input", side_effect=["How many athletes?", "/cancel"])
    def test_cancellation_is_logged_without_execution(self, read_input):
        self.client.responses.parse.return_value = proposal("clarify", sql=None, message="Which population?")
        self.assertEqual(self.run_cli("--execute", "--interactive"), 0)
        self.assertEqual(self.records[-1]["decision"], "cancelled")
        self.assertFalse(self.records[-1]["executed"])
        self.DatabaseConnector.assert_not_called()
        self.client.responses.parse.assert_called_once()

    def test_connection_cleanup_error_is_logged(self):
        self.DatabaseConnector.return_value.close.side_effect = Error("Connection closed")
        self.assertEqual(self.run_cli("--execute", "--prompt", "How many surveys?"), 1)
        self.assertEqual(self.records[-1]["event"], "error")
        self.assertIn("Connection closed", self.records[-1]["message"])
        self.client.responses.create.assert_not_called()

    def test_sql_error_is_logged_without_answer_call_or_retry(self):
        self.cursor.execute.side_effect = [None, None, None, Error("Unknown column", errno=1054)]
        self.assertEqual(self.run_cli("--execute", "--prompt", "How many surveys?"), 1)
        self.assertEqual(self.events[-2:], ["sql_execution", "error"])
        self.assertIn("1054", self.records[-1]["message"])
        self.client.responses.parse.assert_called_once()
        self.client.responses.create.assert_not_called()

    def test_answer_error_preserves_completed_query_and_results(self):
        self.client.responses.create.side_effect = OpenAIError("answer unavailable")
        self.assertEqual(self.run_cli("--execute", "--prompt", "How many surveys?"), 1)
        self.assertIn("sql_results", self.events)
        self.assertNotIn("natural_language", self.events)
        self.assertEqual(self.events[-1], "error")


if __name__ == "__main__":
    unittest.main()
