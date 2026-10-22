import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error
from openai import OpenAIError

from db import DatabaseConnector
from model_provider import create_model_connector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check MySQL and query the configured model.")
    parser.add_argument("--prompt", help="Send a prompt to the configured model provider.")
    args = parser.parse_args(argv)
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

    if args.prompt is not None:
        try:
            with create_model_connector() as connector:
                print(connector.generate_response(args.prompt))
        except (ValueError, RuntimeError, OpenAIError) as exc:
            print(f"Model request failed: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
