import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

from mysql.connector import Error

from ModelConnector import RequestDeniedError, SQLQuery
from sql_execution import (
    MAX_RESULT_BYTES, MAX_ROWS, ResultLimitError, SQLExecutionError,
    bounded_query, execute_query, json_value,
)


ROOT = Path(__file__).resolve().parents[1]


class SQLPolicyTests(unittest.TestCase):
    def test_reviewed_examples_are_accepted(self):
        for example in json.loads((ROOT / "context/sql_examples.json").read_text()):
            with self.subTest(example=example["id"]):
                bounded_query(SQLQuery(example["sql"], tuple(example["parameters"])))

    def test_caps_preserve_original_sql_and_bind_values(self):
        sql = "  SELECT SurveyID FROM Survey WHERE AthleteID = %s ORDER BY SurveyID LIMIT 999;  "
        values = ("x'; DROP TABLE Survey; --",)
        bounded = bounded_query(SQLQuery(sql, values))
        self.assertEqual(bounded.sql, sql.strip().removesuffix(";").replace("999", "101"))
        self.assertEqual(bounded.parameters, values)
        self.assertEqual(bounded_query(SQLQuery("SELECT COUNT(*) AS total FROM Survey;")).sql,
                         "SELECT COUNT(*) AS total FROM Survey LIMIT 101")

    def test_ctes_unions_qualified_tables_and_small_limits(self):
        for sql in (
            "WITH counts AS (SELECT AthleteID, COUNT(*) AS total FROM Survey GROUP BY AthleteID) SELECT AthleteID FROM counts LIMIT 10",
            "SELECT AthleteID FROM Athlete UNION SELECT AthleteID FROM Survey",
            "SELECT SurveyID FROM syp_dev.Survey ORDER BY SurveyID LIMIT 5 OFFSET 2",
            "SELECT SurveyID FROM Survey WHERE AthleteID = 'a; # /* harmless */' LIMIT 10",
        ):
            with self.subTest(sql=sql):
                self.assertTrue(bounded_query(SQLQuery(sql)).sql)

    def test_prohibited_sql_is_rejected(self):
        statements = (
            "DELETE FROM Survey", "UPDATE Athlete SET School = 'bad'",
            "SELECT SurveyID FROM Survey; SELECT AthleteID FROM Athlete",
            "SELECT SurveyID FROM Survey FOR UPDATE",
            "SELECT SurveyID INTO OUTFILE '/tmp/leak' FROM Survey",
            "SELECT SurveyID FROM Survey INTO @x",
            "SELECT @x := SurveyID FROM Survey", "SELECT @@version FROM Survey",
            "SELECT LOAD_FILE('/tmp/secret') FROM Survey",
            "SELECT SLEEP(10) FROM Survey", "SELECT BENCHMARK(10000, 1) FROM Survey",
            "SELECT GET_LOCK('x', 10) FROM Survey", "SELECT custom_function(SurveyID) FROM Survey",
            "SELECT syp_dev.SUM(SurveyID) FROM Survey",
            "SELECT * FROM mysql.user", "SELECT * FROM information_schema.tables",
            "SELECT * FROM other_db.Survey", "SELECT * FROM _prisma_migrations",
            "SELECT * FROM teamcompliancesummary", "SELECT 1",
            "WITH Survey AS (SELECT User FROM mysql.user) SELECT User FROM Survey",
            "WITH RECURSIVE c AS (SELECT AthleteID FROM Athlete) SELECT * FROM c",
            "SELECT /*+ MAX_EXECUTION_TIME(0) */ SurveyID FROM Survey",
            "SELECT SurveyID FROM Survey /*!50000 FOR UPDATE */",
            "SELECT SurveyID FROM Survey -- trailing comment", "SELECT SurveyID FROM Survey #comment",
            "SELECT SQL_CALC_FOUND_ROWS SurveyID FROM Survey",
            'SELECT "SurveyID" FROM Survey', "SELECT SurveyID FROM Survey WHERE AthleteID = 'a\\b'",
            "SELECT SurveyID FROM Survey LIMIT -1", "SELECT SurveyID FROM Survey LIMIT %s",
            "SELECT SurveyID FROM Survey LIMIT 0",
            "SELECT a.SurveyID FROM mysql.user a JOIN Survey a ON 1 = 1",
            "SELECT SurveyID FROM Survey LIMIT 1 OFFSET 999999",
        )
        for sql in statements:
            with self.subTest(sql=sql), self.assertRaises(RequestDeniedError):
                bounded_query(SQLQuery(sql, (10,) if "%s" in sql else ()))

    def test_bad_bind_contract_is_rejected(self):
        for query in (
            SQLQuery("SELECT SurveyID FROM Survey WHERE AthleteID = %s"),
            SQLQuery("SELECT SurveyID FROM Survey", ("extra",)),
            SQLQuery("SELECT SurveyID FROM Survey WHERE AthleteID = '%s'", ("x",)),
            SQLQuery("SELECT SurveyID FROM Survey WHERE AthleteID = ?", ("x",)),
            SQLQuery("SELECT SurveyID FROM Survey WHERE AthleteID = %s", (object(),)),
            SQLQuery("SELECT SurveyID FROM Survey WHERE SurveyID = %s", (float("nan"),)),
        ):
            with self.subTest(query=query), self.assertRaises(RequestDeniedError):
                bounded_query(query)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("sql_execution.DatabaseConnector")
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.database = self.factory.return_value
        self.database.database_name = "syp_dev"
        self.connection = self.database.connect.return_value
        self.cursor = self.connection.cursor.return_value
        self.cursor.column_names = ("SurveyID",)
        self.query = SQLQuery("SELECT SurveyID FROM Survey ORDER BY SurveyID")

    def run_query(self):
        return execute_query(self.query, "mysql://test@localhost/syp_dev")

    def test_read_only_deadline_bound_parameters_and_cleanup(self):
        self.query = SQLQuery("SELECT SurveyID FROM Survey WHERE AthleteID = %s", ("a-one",))
        self.cursor.fetchone.side_effect = [(2,), None]
        result = self.run_query()
        self.assertEqual(result.rows, [{"SurveyID": 2}])
        self.assertFalse(result.truncated)
        self.connection.start_transaction.assert_called_once_with(readonly=True)
        self.cursor.execute.assert_any_call("SET SESSION MAX_EXECUTION_TIME = 5000")
        self.cursor.execute.assert_any_call("SET SESSION lock_wait_timeout = 5")
        self.assertEqual(self.cursor.execute.call_args.args,
                         ("SELECT SurveyID FROM Survey WHERE AthleteID = %s LIMIT 101", ("a-one",)))
        self.database.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_revalidation_blocks_direct_execution(self):
        self.query = SQLQuery("DELETE FROM Survey")
        with self.assertRaises(RequestDeniedError):
            self.run_query()
        self.factory.assert_not_called()

    def test_wrong_database_never_connects(self):
        self.database.database_name = "other_db"
        with self.assertRaisesRegex(SQLExecutionError, "saved schema"):
            self.run_query()
        self.database.connect.assert_not_called()

    def test_empty_rows_and_count_zero_are_distinct(self):
        self.cursor.fetchone.return_value = None
        self.assertEqual(self.run_query().rows, [])
        self.cursor.column_names = ("total",)
        self.cursor.fetchone.side_effect = [(0,), None]
        self.assertEqual(self.run_query().rows, [{"total": 0}])

    def test_reads_one_extra_row_and_never_fetches_all(self):
        self.cursor.fetchone.side_effect = [(i,) for i in range(1000)]
        result = self.run_query()
        self.assertEqual(len(result.rows), MAX_ROWS)
        self.assertTrue(result.truncated)
        self.assertEqual(self.cursor.fetchone.call_count, MAX_ROWS + 1)
        self.cursor.fetchall.assert_not_called()
        self.database.close.assert_called_once()

    def test_explicit_limit_cannot_claim_complete_population(self):
        self.query = SQLQuery("SELECT SurveyID FROM Survey LIMIT 1")
        self.cursor.fetchone.side_effect = [(1,), None]
        self.assertTrue(self.run_query().truncated)

    def test_result_byte_cap_and_oversize_first_row(self):
        self.cursor.fetchone.side_effect = [("x" * 9000,), ("y" * 9000,)]
        result = self.run_query()
        self.assertEqual(len(result.rows), 1)
        self.assertTrue(result.truncated)
        self.assertLess(len(json.dumps(result.rows).encode()), MAX_RESULT_BYTES)
        self.cursor.fetchone.side_effect = [("x" * MAX_RESULT_BYTES,)]
        with self.assertRaisesRegex(ResultLimitError, "One result row"):
            self.run_query()
        self.assertEqual(self.database.close.call_count, 2)

    def test_duplicate_column_names_fail_instead_of_losing_data(self):
        self.cursor.column_names = ("AthleteID", "AthleteID")
        with self.assertRaisesRegex(SQLExecutionError, "Duplicate"):
            self.run_query()
        self.database.close.assert_called_once()

    def test_query_timeout_and_other_database_errors_do_not_retry(self):
        for code, message in ((3024, "five-second"), (1054, "MySQL 1054")):
            with self.subTest(code=code):
                self.cursor.execute.reset_mock()
                self.cursor.execute.side_effect = [None, None, None, Error("broken", errno=code)]
                with self.assertRaisesRegex(SQLExecutionError, message):
                    self.run_query()
                self.assertEqual(self.cursor.execute.call_count, 4)
        self.assertEqual(self.database.close.call_count, 2)

    def test_values_preserve_precision_utc_binary_and_null(self):
        self.assertEqual(json_value(Decimal("9007199254740993.12")), "9007199254740993.12")
        self.assertEqual(json_value(datetime(2026, 10, 22)), "2026-10-22T00:00:00+00:00")
        self.assertEqual(json_value(datetime(2026, 10, 22, tzinfo=timezone.utc)), "2026-10-22T00:00:00+00:00")
        self.assertEqual(json_value(b"hi"), {"base64": "aGk="})
        self.assertIsNone(json_value(None))
        with self.assertRaises(ResultLimitError):
            json_value(float("inf"))


if __name__ == "__main__":
    unittest.main()
