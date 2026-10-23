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
from clarification import ClarificationLimitError, SQLClarificationSession
from experiment_log import ExperimentLog
from model_provider import create_model_connector
from sql_context import load_sql_context
from user_input import InputValidationError, validate_user_input


def _print_sql_outcome(session: SQLClarificationSession, output: dict) -> None:
    output["executed"] = False
    if session.connector.last_usage is not None:
        output["usage"] = session.connector.last_usage.model_dump()
    output["usage_totals"] = session.usage_totals()
    print(json.dumps(output, indent=2))


def generate_sql_preview(connector, prompt: str, reference: datetime | None, interactive: bool,
                         *, execute: bool = False, trace=None) -> int:
    """Share the clarification loop between preview and execution modes."""
    context = load_sql_context() if execute else None
    session = SQLClarificationSession(connector, prompt, context=context, as_of=reference)
    emit = trace.emit if trace is not None else lambda event, **data: None
    if execute:
        emit("user_input", question=prompt, model=connector.model,
             sql_context_key=context.cache_key, reference_time=session.as_of.isoformat())

    def outcome(output):
        output.setdefault("executed", False)
        emit("outcome", **output, sql_usage_totals=session.usage_totals())
        if not execute:
            _print_sql_outcome(session, output)

    while True:
        try:
            emit("model_sql_request", question=session.question,
                 clarifications=[{"question": turn.question, "answer": turn.answer}
                                 for turn in session.turns], request=session.request_count + 1)
            query = session.propose()
        except ClarificationRequiredError as exc:
            emit("model_clarification", text=str(exc),
                 usage=connector.last_usage.model_dump() if connector.last_usage is not None else None)
            if not interactive:
                outcome({"decision": "clarify", "message": str(exc), "executed": False})
                return 0
            print(f"\n{session.pending_question}")
            for _ in range(3):
                try:
                    answer = input("Your answer (or /cancel): ")
                except (EOFError, KeyboardInterrupt) as cancelled:
                    session.cancel()
                    print()
                    outcome({"decision": "cancelled", "message": "Request cancelled."})
                    return 130 if isinstance(cancelled, KeyboardInterrupt) else 0
                emit("user_reply", text=answer)
                if answer.strip().lower() == "/cancel":
                    session.cancel()
                    outcome({"decision": "cancelled", "message": "Request cancelled."})
                    return 0
                try:
                    session.reply(answer)
                except InputValidationError as invalid:
                    print(f"Invalid input: {invalid}")
                    emit("input_rejected", message=str(invalid))
                else:
                    break
            else:
                session.cancel()
                outcome({
                    "decision": "cancelled", "message": "Too many invalid replies. Please start again.",
                })
                return 1
        except ClarificationLimitError as exc:
            outcome({"decision": "clarification_limit", "message": str(exc)})
            return 1
        except RequestDeniedError as exc:
            emit("model_denial", message=str(exc),
                 usage=connector.last_usage.model_dump() if connector.last_usage is not None else None)
            outcome({"decision": "deny", "message": str(exc)})
            return 0
        else:
            emit("model_sql", sql=query.sql, parameters=query.parameters,
                 usage=connector.last_usage.model_dump() if connector.last_usage is not None else None)
            if execute:
                connector.respond_to_query(session.question, query, as_of=session.as_of,
                                           clarifications=session.turns, observe=emit)
                outcome({"decision": "answered", "executed": True})
            else:
                outcome({"decision": "query", "sql": query.sql, "parameters": query.parameters})
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check MySQL and query the configured model.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--prompt", help="Send a prompt to the configured model provider.")
    inputs.add_argument(
        "--interactive", action="store_true", help="Read one prompt from the terminal."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--generate-sql", action="store_true",
        help="Propose SQL using the saved schema; do not execute it.",
    )
    modes.add_argument("--execute", action="store_true",
                       help="Generate and run read-only SQL, explain results, and log the experiment.")
    parser.add_argument("--as-of", help="Timezone-aware ISO reference time for SQL generation.")
    args = parser.parse_args(argv)
    if (args.generate_sql or args.execute) and args.prompt is None and not args.interactive:
        parser.error("--generate-sql/--execute requires --prompt or --interactive.")
    if args.as_of and not (args.generate_sql or args.execute):
        parser.error("--as-of requires --generate-sql or --execute.")
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
    if args.execute:
        try:
            with ExperimentLog() as trace:
                try:
                    with create_model_connector() as connector:
                        return generate_sql_preview(connector, prompt, reference, args.interactive,
                                                    execute=True, trace=trace)
                except (OSError, ValueError, RuntimeError, OpenAIError, Error) as exc:
                    trace.emit("error", error_type=type(exc).__name__, message=str(exc))
                    return 1
                except KeyboardInterrupt:
                    trace.emit("outcome", decision="cancelled", message="Request interrupted.")
                    return 130
        except OSError as exc:
            print(f"Experiment logging failed: {exc}")
            return 1
    if args.generate_sql:
        try:
            with create_model_connector() as connector:
                return generate_sql_preview(connector, prompt, reference, args.interactive)
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
