import unittest
from unittest.mock import patch

from mysql.connector import Error

from db import DatabaseConnector


class DatabaseConnectorTests(unittest.TestCase):
    @patch("db.mysql.connector.connect")
    def test_url_credentials_are_decoded_and_default_port_is_used(self, connect):
        DatabaseConnector("mysql://mcp:p%40ss%23%2F@localhost/syp_dev").connect()
        config = connect.call_args.kwargs
        self.assertEqual(config["password"], "p@ss#/")
        self.assertEqual(config["user"], "mcp")
        self.assertEqual(config["host"], "localhost")
        self.assertEqual(config["database"], "syp_dev")
        self.assertEqual(config["port"], 3306)
        self.assertEqual(config["time_zone"], "+00:00")

    @patch("db.mysql.connector.connect")
    def test_custom_port(self, connect):
        DatabaseConnector("mysql://mcp:password@localhost:3307/syp_dev").connect()
        self.assertEqual(connect.call_args.kwargs["port"], 3307)

    def test_invalid_urls_fail_without_disclosing_credentials(self):
        for url in (
            "", "DATABASE_URL", "postgres://mcp:secret@localhost/db",
            "mysql://mcp:secret@localhost", "mysql://localhost/db",
            "mysql://mcp:secret@localhost:invalid/db",
            "mysql://mcp:secret@localhost:0/db",
            "mysql://mcp:secret@localhost/db?ignored=true",
            "mysql://mcp:secret@localhost/db#fragment",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as error:
                    DatabaseConnector(url)
                self.assertNotIn("secret", str(error.exception))

    @patch("db.mysql.connector.connect")
    def test_context_closes_connection_when_query_fails(self, connect):
        connection = connect.return_value
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            with DatabaseConnector("mysql://mcp:password@localhost/db") as actual:
                self.assertIs(actual, connection)
                raise RuntimeError("query failed")
        connection.close.assert_called_once()
        connection.commit.assert_not_called()

    @patch("db.mysql.connector.connect")
    def test_connection_can_be_reused_and_reopened_after_close(self, connect):
        connection = connect.return_value
        connection.is_connected.return_value = True
        connector = DatabaseConnector("mysql://mcp:password@localhost/db")
        self.assertIs(connector.connect(), connector.connect())
        connect.assert_called_once()
        connector.close()
        connector.close()
        connection.close.assert_called_once()
        connector.connect()
        self.assertEqual(connect.call_count, 2)

    @patch("db.mysql.connector.connect", side_effect=Error("Access denied"))
    def test_connection_errors_propagate(self, connect):
        with self.assertRaisesRegex(Error, "Access denied"):
            DatabaseConnector("mysql://mcp:password@localhost/db").connect()


if __name__ == "__main__":
    unittest.main()
