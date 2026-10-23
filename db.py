"""MySQL connections configured by a DATABASE_URL."""

from urllib.parse import unquote, urlsplit

import mysql.connector


class DatabaseConnector:
    def __init__(self, url: str):
        try:
            parsed = urlsplit(url)
            port = parsed.port if parsed.port is not None else 3306
            valid = (
                parsed.scheme == "mysql"
                and parsed.hostname
                and parsed.username
                and parsed.path.strip("/")
                and not parsed.query
                and not parsed.fragment
                and 1 <= port <= 65535
            )
        except (TypeError, ValueError):
            valid = False

        if not valid:
            raise ValueError(
                "DATABASE_URL must use mysql://user:password@host:port/database "
                "with no query string or fragment. URL-encode special characters "
                "in the username and password."
            )

        self._config = {
            "host": parsed.hostname,
            "port": port,
            "user": unquote(parsed.username),
            "password": unquote(parsed.password or ""),
            "database": unquote(parsed.path[1:]),
            "charset": "utf8mb4",
            "connection_timeout": 5,
            "read_timeout": 10,
            "write_timeout": 10,
            "autocommit": False,
            "time_zone": "+00:00",
        }
        self._connection = None

    @property
    def database_name(self) -> str:
        return self._config["database"]

    def connect(self):
        """Return a live connection; the caller must commit any writes."""
        if self._connection is None or not self._connection.is_connected():
            self._connection = mysql.connector.connect(**self._config)
        return self._connection

    def close(self):
        """Release the connection, rolling back uncommitted work."""
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
