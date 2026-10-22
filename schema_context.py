"""Collect a stable snapshot of the configured database's complete schema."""

import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error

from db import DatabaseConnector


PROJECT_ROOT = Path(__file__).resolve().parent
CONTEXT_DIR = PROJECT_ROOT / "context"


def collect_schema(connection) -> dict:
    """Use trusted metadata queries, independently of user-input validation."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        database = cursor.fetchone()[0]
        cursor.execute("SHOW TABLES")
        tables = sorted(row[0] for row in cursor.fetchall())
        definitions = {}
        unavailable_definitions = {}
        for table in tables:
            quoted_table = "`" + table.replace("`", "``") + "`"
            try:
                cursor.execute(f"SHOW CREATE TABLE {quoted_table}")
            except Error as exc:
                if exc.errno != 1142:
                    raise
                cursor.execute(f"SHOW COLUMNS FROM {quoted_table}")
                columns = [dict(zip(cursor.column_names, row)) for row in cursor.fetchall()]
                unavailable_definitions[table] = {
                    "reason": "SHOW CREATE TABLE denied; full definition unavailable",
                    "columns": columns,
                }
                continue
            ddl = cursor.fetchone()[1]
            # Strip only the changing table-option counter, not the column's
            # AUTO_INCREMENT attribute. Preserve raw DDL everywhere else.
            ddl = re.sub(r"(?m)^(\).*) AUTO_INCREMENT=\d+(?=\s|$)", r"\1", ddl)
            definitions[table] = ddl
    return {"format_version": 1, "database": database, "show_tables": tables,
            "create_tables": definitions, "unavailable_definitions": unavailable_definitions}


def save_schema(snapshot: dict, destination: Path = CONTEXT_DIR / "schema.json") -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the full local MySQL schema snapshot.")
    parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        parser.error("Set DATABASE_URL before collecting schema.")
    try:
        with DatabaseConnector(url) as connection:
            snapshot = collect_schema(connection)
        save_schema(snapshot)
    except (Error, ValueError, OSError) as exc:
        print(f"Schema refresh failed: {exc}")
        return 1
    print(f"Saved {len(snapshot['show_tables'])} tables from {snapshot['database']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
