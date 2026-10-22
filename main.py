import os
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error

from db import DatabaseConnector


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Set DATABASE_URL in .env before starting the application.")
        return 1

    try:
        with DatabaseConnector(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE(), VERSION(), 1")
                database, version, result = cursor.fetchone()
                if result != 1:
                    raise RuntimeError("The database connection check failed.")
                print(f"Connected to MySQL {version}; database: {database}")
    except (ValueError, Error) as exc:
        print(f"Database connection failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
