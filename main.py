import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error
from openai import OpenAIError

from db import DatabaseConnector
from model_provider import create_model_connector
from user_input import InputValidationError, validate_user_input


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check MySQL and query the configured model.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--prompt", help="Send a prompt to the configured model provider.")
    inputs.add_argument(
        "--interactive", action="store_true", help="Read one prompt from the terminal."
    )
    args = parser.parse_args(argv)

    # Validate before loading credentials, opening MySQL, or creating a client.
    prompt = args.prompt
    if args.interactive:
        try:
            prompt = input("Enter your question: ")
        except EOFError:
            print("No input received.")
            return 0
        except KeyboardInterrupt:
            print("\nRequest cancelled.")
            return 130
    if prompt is not None:
        try:
            prompt = validate_user_input(prompt)
        except InputValidationError as exc:
            print(f"Invalid input: {exc}")
            return 1

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

    if prompt is not None:
        try:
            with create_model_connector() as connector:
                print(connector.generate_response(prompt))
        except (ValueError, RuntimeError, OpenAIError) as exc:
            print(f"Model request failed: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
