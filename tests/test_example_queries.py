"""Portable query-logic regression tests; MySQL verification is a separate CLI."""

import json
import sqlite3
import unittest
from datetime import datetime, timedelta

from sql_context import PROJECT_ROOT


class ExampleQueryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        fixtures = PROJECT_ROOT / "tests/fixtures"
        self.connection.executescript((fixtures / "survey_data.sql").read_text())
        examples = json.loads((PROJECT_ROOT / "context/sql_examples.json").read_text())
        self.examples = {example["id"]: example for example in examples}
        self.cases = json.loads((fixtures / "query_cases.json").read_text())
        reference = datetime.fromisoformat(self.cases["reference_time_utc"])
        self.variables = {
            "$as_of": reference.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "$seven_days_ago": (reference - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S.%f"),
        }

    def run_example(self, identifier, parameters=None):
        example = self.examples[identifier]
        values = parameters if parameters is not None else [self.variables.get(value, value) for value in example["parameters"]]
        # Only the placeholder syntax changes; the SQL logic is identical.
        cursor = self.connection.execute(example["sql"].replace("%s", "?"), values)
        return [dict(row) for row in cursor.fetchall()]

    def test_five_example_outputs_on_fixed_fixture(self):
        for case in self.cases["cases"]:
            with self.subTest(example=case["id"]):
                self.assertEqual(self.run_example(case["id"]), case["expected"])

    def test_unknown_team_is_distinct_from_known_empty_team(self):
        for identifier, column in (("team_submission_count", "SurveyCount"),
                                   ("team_athlete_count", "AthleteCount")):
            with self.subTest(example=identifier):
                self.assertEqual(self.run_example(identifier, ["NONE00"]), [])
                self.assertEqual(self.run_example(identifier, ["EMPTY0"]), [{"TeamID": "EMPTY0", column: 0}])

    def test_no_submissions_does_not_report_zero_count_leaders(self):
        self.connection.execute("DELETE FROM Survey")
        self.assertEqual(self.run_example("most_submissions"), [])
        self.assertEqual(self.run_example("no_submissions"), [
            {"AthleteID": identifier} for identifier in ("a-one", "a-other", "a-two", "a-zero")
        ])

    def test_no_membership_prevents_inclusion_in_active_population(self):
        self.connection.execute("DELETE FROM Compliance")
        self.assertEqual(self.run_example("most_submissions"), [])
        self.assertEqual(self.run_example("no_submissions"), [])


if __name__ == "__main__":
    unittest.main()
