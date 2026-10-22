import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error
from openai import OpenAIError

from db import DatabaseConnector
from ModelConnector import ClarificationRequiredError, RequestDeniedError
from model_provider import create_model_connector
from user_input import InputValidationError, validate_user_input


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check MySQL and query the configured model.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--prompt", help="Send a prompt to the configured model provider.")
    inputs.add_argument(
        "--interactive", action="store_true", help="Read one prompt from the terminal."
    )
    parser.add_argument(
        "--generate-sql", action="store_true",
        help="Propose SQL using the saved schema; do not execute it.",
    )
    parser.add_argument("--as-of", help="Timezone-aware ISO reference time for SQL generation.")
    args = parser.parse_args(argv)
    if args.generate_sql and args.prompt is None and not args.interactive:
        parser.error("--generate-sql requires --prompt or --interactive.")
    if args.as_of and not args.generate_sql:
        parser.error("--as-of requires --generate-sql.")
    reference = None
    if args.as_of:
        try:
            reference = datetime.fromisoformat(args.as_of)
            if reference.tzinfo is None or reference.utcoffset() is None:
                raise ValueError
        except ValueError:
            parser.error("--as-of must be an ISO timestamp with a timezone.")

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
    if args.generate_sql:
        try:
            with create_model_connector() as connector:
                query = connector.generate_sql(prompt, as_of=reference)
                output = {"decision": "query", "sql": query.sql,
                          "parameters": query.parameters, "executed": False}
                if connector.last_usage is not None:
                    output["usage"] = connector.last_usage.model_dump()
                print(json.dumps(output, indent=2))
            return 0
        except (RequestDeniedError, ClarificationRequiredError) as exc:
            decision = "deny" if isinstance(exc, RequestDeniedError) else "clarify"
            output = {"decision": decision, "message": str(exc), "executed": False}
            if connector.last_usage is not None:
                output["usage"] = connector.last_usage.model_dump()
            print(json.dumps(output, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, OpenAIError) as exc:
            print(f"SQL generation failed: {exc}")
            return 1

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
