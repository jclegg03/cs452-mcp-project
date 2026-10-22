"""Read-only verification of the checked-in example SQL, never model output."""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error

from db import DatabaseConnector


ROOT = Path(__file__).resolve().parent


def verify(connection, examples: list[dict], reference: datetime, team_id: str) -> list[dict]:
    """Execute developer-reviewed examples under a fixed reference time."""
    reference = reference.astimezone(timezone.utc).replace(tzinfo=None)
    variables = {"as_of": reference, "seven_days_ago": reference - timedelta(days=7)}
    results = []
    with connection.cursor(dictionary=True) as cursor:
        cursor.execute("SET SESSION MAX_EXECUTION_TIME = 5000")
        connection.start_transaction(readonly=True, consistent_snapshot=True)
        for example in examples:
            parameters = [variables[value[1:]] if isinstance(value, str) and value.startswith("$")
                          else team_id if value == "TEAM-X" else value
                          for value in example["parameters"]]
            cursor.execute(example["sql"], tuple(parameters))
            rows = cursor.fetchmany(101)
            if len(rows) > 100:
                raise RuntimeError(f"Example {example['id']} exceeded the 100-row verification limit.")
            results.append({"id": example["id"], "parameters": parameters, "rows": rows})
    connection.rollback()
    return results


def inspect(connection) -> dict:
    """Small aggregate diagnostics; no raw survey JSON or credentials."""
    statements = {
        "timezone": "SELECT @@session.time_zone AS session_timezone, @@global.time_zone AS server_timezone",
        "submissions": "SELECT COUNT(*) AS count, MIN(SubmissionDate) AS first_submission, MAX(SubmissionDate) AS last_submission FROM Survey",
        "population": "SELECT COUNT(*) AS athletes, SUM(Deactivated IS NULL) AS not_deactivated FROM Athlete",
        "memberships": "SELECT IsActive, COUNT(*) AS memberships FROM Compliance GROUP BY IsActive ORDER BY IsActive",
        "teams": "SELECT TeamID FROM Team ORDER BY TeamID LIMIT 5",
    }
    result = {}
    with connection.cursor(dictionary=True) as cursor:
        cursor.execute("SET SESSION MAX_EXECUTION_TIME = 5000")
        for key, sql in statements.items():
            cursor.execute(sql)
            result[key] = cursor.fetchall()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Inspect aggregate data shapes.")
    parser.add_argument("--as-of", default="2026-10-22T00:00:00+00:00")
    parser.add_argument("--team-id", default="TEAM-X", help="Team ID for the two team examples.")
    args = parser.parse_args()
    reference = datetime.fromisoformat(args.as_of)
    if reference.tzinfo is None:
        parser.error("--as-of must include a timezone.")
    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        parser.error("Set DATABASE_URL before verifying examples.")
    try:
        with DatabaseConnector(url) as connection:
            if args.inspect:
                result = inspect(connection)
            else:
                examples = json.loads((ROOT / "context/sql_examples.json").read_text())
                result = verify(connection, examples, reference, args.team_id)
        print(json.dumps(result, indent=2, default=str))
    except (Error, ValueError, RuntimeError) as exc:
        print(f"Example verification failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
