import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from ModelConnector import ClarificationRequiredError, RequestDeniedError, SQLQuery
from clarification import ClarificationLimitError, SQLClarificationSession
from user_input import InputValidationError


class ClarificationSessionTests(unittest.TestCase):
    def setUp(self):
        self.connector = Mock()
        self.connector.last_usage = None
        self.reference = datetime(2026, 10, 22, tzinfo=timezone.utc)

    def session(self, question="How many surveys?"):
        return SQLClarificationSession(self.connector, question, as_of=self.reference)

    def test_reply_resumes_original_question_with_a_fixed_clock(self):
        query = SQLQuery("SELECT COUNT(*) FROM Survey")
        self.connector.generate_sql.side_effect = [ClarificationRequiredError("Which team?"), query]
        session = self.session()
        with self.assertRaises(ClarificationRequiredError):
            session.propose()
        session.reply(" TEAM-X ")
        self.assertEqual(session.propose(), query)
        first, second = self.connector.generate_sql.call_args_list
        self.assertEqual(first.args[0], second.args[0])
        self.assertEqual(first.kwargs["as_of"], second.kwargs["as_of"])
        self.assertEqual(first.kwargs["clarifications"], ())
        turn = second.kwargs["clarifications"][0]
        self.assertEqual((turn.question, turn.answer), ("Which team?", "TEAM-X"))
        self.assertTrue(session.finished)

    def test_invalid_reply_preserves_pending_question_and_makes_no_request(self):
        self.connector.generate_sql.side_effect = ClarificationRequiredError("Which team?")
        session = self.session()
        with self.assertRaises(ClarificationRequiredError):
            session.propose()
        for answer in (" ", "SELECT * FROM Survey", "x" * 2001, "TEAM-X\x1b"):
            with self.subTest(answer=answer[:30]):
                with self.assertRaises(InputValidationError):
                    session.reply(answer)
                self.assertEqual(session.pending_question, "Which team?")
                self.assertEqual(session.turns, ())
        self.connector.generate_sql.assert_called_once()

    def test_cannot_spend_another_call_while_waiting_for_a_reply(self):
        self.connector.generate_sql.side_effect = ClarificationRequiredError("Which team?")
        session = self.session()
        with self.assertRaises(ClarificationRequiredError):
            session.propose()
        with self.assertRaisesRegex(RuntimeError, "pending clarification"):
            session.propose()
        self.assertEqual(session.request_count, 1)

    def test_three_replies_bound_a_never_resolved_request_to_four_calls(self):
        self.connector.generate_sql.side_effect = ClarificationRequiredError("Please be more specific.")
        session = self.session()
        for _ in range(3):
            with self.assertRaises(ClarificationRequiredError):
                session.propose()
            session.reply("Active athletes on TEAM-X.")
        with self.assertRaises(ClarificationLimitError):
            session.propose()
        with self.assertRaisesRegex(RuntimeError, "already ended"):
            session.propose()
        self.assertEqual(self.connector.generate_sql.call_count, 4)

    def test_cumulative_character_budget_rejects_reply_without_mutating_history(self):
        self.connector.generate_sql.side_effect = ClarificationRequiredError("q" * 500)
        session = self.session("x" * 2000)
        for _ in range(2):
            with self.assertRaises(ClarificationRequiredError):
                session.propose()
            session.reply("a" * 2000)
        with self.assertRaises(ClarificationRequiredError):
            session.propose()
        before = session.turns
        with self.assertRaisesRegex(InputValidationError, "history is too long"):
            session.reply("a" * 2000)
        self.assertEqual(session.turns, before)
        self.assertIsNotNone(session.pending_question)

    def test_denial_and_cancellation_are_terminal(self):
        self.connector.generate_sql.side_effect = RequestDeniedError("Database questions only.")
        session = self.session()
        with self.assertRaises(RequestDeniedError):
            session.propose()
        self.assertTrue(session.finished)
        session = self.session()
        session.cancel()
        with self.assertRaisesRegex(RuntimeError, "already ended"):
            session.propose()

    def test_new_request_does_not_inherit_prior_replies(self):
        self.connector.generate_sql.side_effect = [ClarificationRequiredError("Which team?"), SQLQuery("SELECT 1")]
        first = self.session()
        with self.assertRaises(ClarificationRequiredError):
            first.propose()
        first.reply("TEAM-X")
        first.cancel()
        second = self.session("How many athletes?")
        second.propose()
        self.assertEqual(self.connector.generate_sql.call_args.kwargs["clarifications"], ())

    def test_usage_totals_include_all_clarification_calls(self):
        self.connector.last_usage = SimpleNamespace(model_dump=lambda: {
            "input_tokens": 100, "input_tokens_details": {"cached_tokens": 80}, "output_tokens": 10,
        })
        self.connector.generate_sql.side_effect = [ClarificationRequiredError("Which team?"), SQLQuery("SELECT 1")]
        session = self.session()
        with self.assertRaises(ClarificationRequiredError):
            session.propose()
        session.reply("TEAM-X")
        session.propose()
        self.assertEqual(session.usage_totals(), {
            "requests": 2, "input_tokens": 200, "cached_input_tokens": 160, "output_tokens": 20,
        })


if __name__ == "__main__":
    unittest.main()
