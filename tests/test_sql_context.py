import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from sql_context import PROJECT_ROOT, SQLProposal, build_sql_context


class SQLContextTests(unittest.TestCase):
    def test_full_schema_and_five_examples_are_present_without_expected_results(self):
        context = build_sql_context()
        snapshot = json.loads((PROJECT_ROOT / "context/schema.json").read_text())
        examples = json.loads((PROJECT_ROOT / "context/sql_examples.json").read_text())
        self.assertEqual(len(examples), 5)
        for table in snapshot["show_tables"]:
            self.assertIn(table, context.prefix)
        for example in examples:
            self.assertIn(example["id"], context.prefix)
            self.assertNotIn("expected", example)
        self.assertNotIn('"expected":', context.prefix)
        self.assertIn("unconfirmed:", context.prefix)

    def test_reference_time_and_question_do_not_change_cache_key_or_prefix(self):
        context = build_sql_context()
        before = (context.prefix, context.cache_key)
        first = json.loads(context.user_message("How many surveys?", datetime(2026, 10, 22, tzinfo=timezone.utc)))
        second = json.loads(context.user_message("How many athletes?", datetime(2026, 10, 23, tzinfo=timezone.utc)))
        self.assertNotEqual(first, second)
        self.assertEqual((context.prefix, context.cache_key), before)
        self.assertEqual(first["available_bind_values"]["last_7_days_start_utc"], "2026-10-15 00:00:00.000000")
        self.assertIn("only if requested", first["clock_note"])

    def test_rejects_naive_reference_time(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_sql_context().user_message("How many surveys?", datetime(2026, 10, 22))

    def test_context_key_changes_when_rules_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(PROJECT_ROOT / "context", root / "context")
            shutil.copytree(PROJECT_ROOT / "prompts", root / "prompts")
            before = build_sql_context(root)
            rules = root / "context/domain_rules.yaml"
            rules.write_text(rules.read_text() + "\nadditional_rule: test\n")
            self.assertNotEqual(before.cache_key, build_sql_context(root).cache_key)

    def test_schema_map_order_does_not_change_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(PROJECT_ROOT / "context", root / "context")
            shutil.copytree(PROJECT_ROOT / "prompts", root / "prompts")
            before = build_sql_context(root)
            path = root / "context/schema.json"
            schema = json.loads(path.read_text())
            schema["create_tables"] = dict(reversed(list(schema["create_tables"].items())))
            path.write_text(json.dumps(schema))
            self.assertEqual(before, build_sql_context(root))

    def test_proposal_consistency_checks(self):
        for proposal in (
            {"decision": "query", "sql": "SELECT %s", "parameters": [], "message": None},
            {"decision": "query", "sql": "", "parameters": [], "message": None},
            {"decision": "deny", "sql": "SELECT 1", "parameters": [], "message": "Denied"},
            {"decision": "clarify", "sql": None, "parameters": [], "message": " "},
        ):
            with self.subTest(proposal=proposal):
                with self.assertRaises(ValidationError):
                    SQLProposal.model_validate(proposal)


if __name__ == "__main__":
    unittest.main()
