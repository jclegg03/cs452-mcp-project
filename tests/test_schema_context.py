import unittest
from unittest.mock import MagicMock

from mysql.connector import Error

from schema_context import collect_schema


class SchemaCollectionTests(unittest.TestCase):
    def test_quotes_table_names_and_removes_only_volatile_counter(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [("z",), ("a`b",)]
        ddl = "CREATE TABLE `a` (\n  `id` int AUTO_INCREMENT\n) ENGINE=InnoDB AUTO_INCREMENT=123 DEFAULT CHARSET=utf8mb4"
        cursor.fetchone.side_effect = [("example",), ("a`b", ddl), ("z", ddl)]
        snapshot = collect_schema(connection)
        self.assertEqual(snapshot["show_tables"], ["a`b", "z"])
        self.assertIn("AUTO_INCREMENT\n", snapshot["create_tables"]["a`b"])
        self.assertNotIn("AUTO_INCREMENT=123", snapshot["create_tables"]["a`b"])
        cursor.execute.assert_any_call("SHOW CREATE TABLE `a``b`")

    def test_permission_denied_definition_has_explicit_column_fallback(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = [None, None, Error("SHOW VIEW denied", errno=1142), None]
        cursor.fetchone.return_value = ("example",)
        cursor.fetchall.side_effect = [[("restricted_view",)], [("id", "int")]]
        cursor.column_names = ("Field", "Type")
        snapshot = collect_schema(connection)
        self.assertNotIn("restricted_view", snapshot["create_tables"])
        fallback = snapshot["unavailable_definitions"]["restricted_view"]
        self.assertEqual(fallback["columns"], [{"Field": "id", "Type": "int"}])
        self.assertIn("unavailable", fallback["reason"])

    def test_other_database_errors_are_not_hidden_by_fallback(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = [None, None, Error("Lost connection", errno=2013)]
        cursor.fetchone.return_value = ("example",)
        cursor.fetchall.return_value = [("a",)]
        with self.assertRaises(Error):
            collect_schema(connection)


if __name__ == "__main__":
    unittest.main()
