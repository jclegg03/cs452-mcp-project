"""Independent SQL policy and bounded execution for model-generated queries."""

import base64
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path

from mysql.connector import Error
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from db import DatabaseConnector
from ModelConnector import QueryResult, RequestDeniedError, SQLQuery


MAX_ROWS = 100
MAX_RESULT_BYTES = 16_384
MAX_EXECUTION_MS = 5000
SCHEMA_PATH = Path(__file__).with_name("context") / "schema.json"
SAFE_FUNCTIONS = frozenset("""ABS AND OR CASE EXISTS AVG CAST CEIL CEILING COALESCE CONCAT CONCAT_WS COUNT DATE DATE_ADD DATE_DIFF
DATE_FORMAT DATE_SUB DAY DAY_OF_MONTH DAY_OF_WEEK DAY_OF_YEAR DENSE_RANK EXTRACT
FLOOR GREATEST HOUR IF IFNULL JSON_EXTRACT JSON_UNQUOTE JSON_LENGTH LAG LEAD
LEAST LEFT LENGTH LOWER MAX MIN MINUTE MOD MONTH NULLIF RANK REPLACE RIGHT ROUND
ROW_NUMBER SECOND SIGN STDDEV_POP STDDEV_SAMP STR_TO_DATE SUBSTRING SUM TIME
TIMESTAMP TIMESTAMPDIFF TIMESTAMP_DIFF TRIM TS_OR_DS_TO_DATE UPPER WEEK YEAR
""".split())


class SQLExecutionError(RuntimeError):
    """A database query failed or exceeded the execution budget."""


class ResultLimitError(RuntimeError):
    """Even one complete result row cannot fit the answer budget."""


def _normalized_sql(sql: str) -> tuple[str, int]:
    """Normalize bind markers for the parser, preserving original character offsets.

    Keep the original statement for the driver. Restrict quoting and comments so
    MySQL SQL modes/executable comments cannot change what the parser validated.
    """
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 10_000:
        raise RequestDeniedError("SQL is empty or exceeds the statement size limit.")
    output = []
    quote = None
    count = 0
    index = 0
    while index < len(sql):
        char = sql[index]
        pair = sql[index:index + 2]
        if ord(char) < 32 and char not in "\n\r\t":
            raise RequestDeniedError("SQL contains unsupported control characters.")
        if quote:
            if char == "\\" or pair == "%s":
                raise RequestDeniedError("Use separate bound values instead of escaped SQL literals.")
            output.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(quote)
                    index += 1
                else:
                    quote = None
        else:
            if char == '"' or char == "\\" or char == "?":
                raise RequestDeniedError("Use backtick identifiers and %s bound values.")
            if char == "#" or pair in ("--", "/*", "*/"):
                raise RequestDeniedError("SQL comments and optimizer hints are not allowed.")
            if char in ("'", "`"):
                quote = char
            if pair == "%s":
                output.append("? ")
                count += 1
                index += 2
                continue
            output.append(char)
        index += 1
    if quote:
        raise RequestDeniedError("SQL contains an unterminated quoted value.")
    return "".join(output), count


def bounded_query(query: SQLQuery, *, schema_path: Path = SCHEMA_PATH) -> SQLQuery:
    """Reject unsafe statements and return original SQL with a top-level row cap."""
    if not isinstance(query, SQLQuery):
        raise RequestDeniedError("Expected a structured SQL query.")
    normalized, count = _normalized_sql(query.sql)
    if not isinstance(query.parameters, (tuple, list)) or count != len(query.parameters):
        raise RequestDeniedError("SQL bind values do not match the placeholders.")
    if len(query.parameters) > 100:
        raise RequestDeniedError("Too many SQL parameters.")
    for value in query.parameters:
        if type(value) not in (str, int, float, bool, type(None)):
            raise RequestDeniedError("Unsupported SQL parameter type.")
        if isinstance(value, str) and len(value) > 2000:
            raise RequestDeniedError("SQL parameter exceeds the size limit.")
        if isinstance(value, float) and not math.isfinite(value):
            raise RequestDeniedError("SQL parameters must be finite.")
    try:
        statements = sqlglot.parse(normalized, read="mysql")
    except sqlglot.errors.SqlglotError as exc:
        raise RequestDeniedError("The generated SQL could not be parsed safely.") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise RequestDeniedError("Only one SELECT or WITH ... SELECT query is allowed.")
    tree = statements[0]
    forbidden = (exp.DDL, exp.DML, exp.Command, exp.Lock, exp.Into, exp.Parameter,
                 exp.SessionParameter, exp.PropertyEQ, exp.Hint, exp.UserDefinedFunction)
    nodes = list(tree.walk())
    if len(nodes) > 1000 or sum(isinstance(node, exp.Join) for node in nodes) > 12:
        raise RequestDeniedError("The query is too complex.")
    for node in nodes:
        if isinstance(node, forbidden):
            raise RequestDeniedError("The query contains a prohibited SQL operation.")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise RequestDeniedError("Recursive queries are not allowed.")
        if isinstance(node, exp.Select) and node.args.get("operation_modifiers"):
            raise RequestDeniedError("SELECT modifiers are not allowed.")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise RequestDeniedError("Schema-qualified stored functions are not allowed.")
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
            if name not in SAFE_FUNCTIONS:
                raise RequestDeniedError(f"The SQL function {name} is not enabled.")
    schema = json.loads(schema_path.read_text())
    allowed = set(schema["create_tables"]) - {"_prisma_migrations"}
    physical_tables = 0
    try:
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    if (not isinstance(source.this, exp.Identifier) or source.catalog
                            or source.name not in allowed
                            or source.db not in ("", schema["database"])):
                        raise RequestDeniedError("The query references a table outside the application schema.")
                    physical_tables += 1
    except sqlglot.errors.SqlglotError as exc:
        raise RequestDeniedError("The query has ambiguous or unsupported table sources.") from exc
    # Check every Table too: table functions and unusual source constructs cannot
    # silently evade the scope-based physical-table check.
    if not physical_tables or any(not isinstance(table.this, exp.Identifier)
                                  for table in tree.find_all(exp.Table)):
        raise RequestDeniedError("A query must read the supplied application tables.")
    for offset in tree.find_all(exp.Offset):
        value = offset.expression
        if not isinstance(value, exp.Literal) or not value.is_int or not 0 <= int(value.this) <= 10_000:
            raise RequestDeniedError("SQL offsets must be literal integers from 0 to 10000.")
    sql = query.sql.strip().removesuffix(";").rstrip()
    limit = tree.args.get("limit")
    if limit is None:
        sql += f" LIMIT {MAX_ROWS + 1}"
    else:
        value = limit.expression
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) <= 0:
            raise RequestDeniedError("The SQL row limit must be a positive literal integer.")
        if int(value.this) > MAX_ROWS + 1:
            # Preserve exact driver SQL and parameter order, changing only the
            # validated literal. Parser offsets refer to the untrimmed original.
            sql = (query.sql[:value.meta["start"]] + str(MAX_ROWS + 1)
                   + query.sql[value.meta["end"] + 1:]).strip().removesuffix(";").rstrip()
    return SQLQuery(sql, tuple(query.parameters))


def json_value(value):
    """Represent database values without precision loss or invalid JSON numbers."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        utc = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return utc.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ResultLimitError("The query returned an unsupported value; choose different columns.")


def execute_query(query: SQLQuery, database_url: str, *, observe=None) -> QueryResult:
    """Validate again at the DB boundary, run once, and discard the connection."""
    query = bounded_query(query)
    database = DatabaseConnector(database_url)
    expected_database = json.loads(SCHEMA_PATH.read_text())["database"]
    if database.database_name != expected_database:
        raise SQLExecutionError("DATABASE_URL does not match the saved schema; refresh context/schema.json.")
    try:
        connection = database.connect()
        cursor = connection.cursor()
        cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {MAX_EXECUTION_MS}")
        cursor.execute("SET SESSION lock_wait_timeout = 5")
        cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
        connection.start_transaction(readonly=True)
        if observe:
            observe("sql_execution", sql=query.sql, parameters=query.parameters,
                    timeout_ms=MAX_EXECUTION_MS, row_limit=MAX_ROWS, byte_limit=MAX_RESULT_BYTES)
        cursor.execute(query.sql, query.parameters)
        columns = list(cursor.column_names)
        if len(columns) != len(set(columns)):
            raise SQLExecutionError("Duplicate result column names; add distinct SQL aliases.")
        rows = []
        size = len(json.dumps(columns).encode()) + 2
        truncated = False
        for _ in range(MAX_ROWS + 1):
            values = cursor.fetchone()
            if values is None:
                break
            if len(rows) >= MAX_ROWS:
                truncated = True
                break
            row = dict(zip(columns, (json_value(value) for value in values)))
            row_size = len(json.dumps(row, ensure_ascii=True, allow_nan=False).encode()) + 2
            if size + row_size > MAX_RESULT_BYTES:
                if not rows:
                    raise ResultLimitError("One result row exceeds 16 KiB; request fewer or smaller columns.")
                truncated = True
                break
            size += row_size
            rows.append(row)
        # A model-supplied LIMIT may hide additional rows. Be conservative about
        # completeness whenever the returned rows reach that limit.
        parsed, _ = _normalized_sql(query.sql)
        cap = int(sqlglot.parse_one(parsed, read="mysql").args["limit"].expression.this)
        truncated = truncated or (cap > 0 and len(rows) >= cap)
        return QueryResult(rows, truncated, columns, query.sql, query.parameters)
    except Error as exc:
        if exc.errno == 3024:
            raise SQLExecutionError("The SQL query exceeded the five-second execution limit.") from exc
        raise SQLExecutionError(f"The SQL query failed (MySQL {exc.errno}): {exc.msg}") from exc
    finally:
        # A dedicated connection avoids unread-result errors when a row/byte cap
        # stops fetching early; disconnect also ends the read-only transaction.
        database.close()
