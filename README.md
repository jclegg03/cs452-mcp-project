# MySQL and model providers

Use Python 3.10 or newer. Create a virtual environment and install the dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If `.env` does not exist, copy `.env.example` to `.env`. Set `DATABASE_URL`
to the existing MySQL user and database:

```dotenv
DATABASE_URL=mysql://mcp:your_password@127.0.0.1:3306/syp_dev
```

The port defaults to 3306. URL-encode special characters in the username or
password, such as `@` as `%40`, `#` as `%23`, and `/` as `%2F`.
Query parameters are not supported. Existing environment variables take precedence
over `.env`; the application finds `.env` beside `main.py`, regardless of the
working directory. Keep credentials in the ignored `.env` file.

With MySQL running and the configured database already created, check connectivity:

```sh
python main.py
```

This runs a read-only query and prints the selected database and server version.
Failures exit with status 1. It does not create tables or change existing data.

Use the connector from application code:

```python
from db import DatabaseConnector

with DatabaseConnector(database_url) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT %s", ("hello",))
        print(cursor.fetchone())
```

Use parameterized queries for values. Writes require `connection.commit()`;
closing the context releases the connection and discards uncommitted changes.
Every connection uses the `+00:00` session timezone, matching the confirmed UTC
convention of the source application.
The driver options follow the [MySQL Connector/Python documentation](https://dev.mysql.com/doc/connector-python/en/connector-python-connectargs.html).

## OpenAI provider

Configure the model provider in `.env`:

```dotenv
MODEL_PROVIDER=openai
MODEL_API_KEY=your_openai_api_key
MODEL_NAME=gpt-5-mini
```

`MODEL_PROVIDER=openai` selects `OpenAIModelConnector`, which extends the abstract
`ModelConnector` interface. `MODEL_API_KEY` is required; `MODEL_NAME` defaults to
`gpt-5-mini` when omitted. Unsupported providers and empty configuration values
raise clear errors.

To check the database and then send a prompt:

```sh
python main.py --prompt "Say hello in one sentence."
```

Running without either input option only checks MySQL. The model request uses the official
OpenAI SDK's [Responses API](https://developers.openai.com/api/reference/python)
and returns its text output. The provider smoke test is a single prompt; SQL
generation supports the bounded clarification exchange described below.

Alternatively, collect one question from the terminal:

```sh
python main.py --interactive
```

`--prompt` and `--interactive` are mutually exclusive. Both run the provider
smoke test unless combined with `--generate-sql` or `--execute`, described below. Interactive
mode reads one line. EOF exits without a request; Ctrl-C exits with status 130.

The CLI validates input before database access or client creation. Input must
be text, contain something besides whitespace, and be at most 2,000 characters
including surrounding whitespace. Terminal control characters and invalid Unicode
surrogates are rejected; tabs, line breaks, normal punctuation, and Unicode text
are preserved. Only surrounding whitespace is removed. The connector applies the
same checks to direct Python calls before making a model request.

The validator also rejects recognizable SQL statements and common injection
fragments, even inside prose or code fences. Detection checks case-insensitively
and handles block-comment obfuscation, MySQL executable comments, full-width
characters, invisible formatting characters, and up to two percent-decoding
passes. Only a detection copy is normalized; accepted input is never decoded or
rewritten. Quotes, semicolons, and isolated words such as `select` or `update`
are allowed when they do not form a recognized SQL pattern. Rejections ask the
user to describe the question in plain English.

This is a conservative heuristic: quoted SQL examples are also rejected, some
SQL-shaped English can require rephrasing, and unrecognized or more heavily
encoded SQL can evade it. It does not replace the generated-SQL validator
or database permissions. See [OWASP's SQL injection prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)
for execution-layer defenses.

The character cap limits input size, not an exact token budget. Validation does
not classify relevance or detect arbitrary prompt injection. User text remains
in the Responses API's `input`, separate from future trusted application
instructions, following the [OpenAI prompt-injection guidance](https://developers.openai.com/api/docs/guides/agent-builder-safety).

To use the provider directly, load the environment before calling the factory:

```python
from pathlib import Path
from dotenv import load_dotenv
from model_provider import create_model_connector

load_dotenv(Path(".env"))
with create_model_connector() as connector:
    print(connector.generate_response("Say hello."))
```

## SQL generation and saved context

Propose a query without executing it:

```sh
python main.py --generate-sql --prompt "How many surveys have been submitted in the last 7 days?"
```

Add `--as-of 2026-10-22T00:00:00+00:00` for a fixed reference time, or combine
`--generate-sql` with `--interactive`. Generation uses the saved context and does
not open MySQL. Its JSON output contains the proposed SQL, separately bound
parameter values, `executed: false`, and API usage including cached input tokens.
Query, deny, and clarify are separate structured decisions. Incomplete model
responses, API refusals, and malformed output are handled without executing SQL.
The preview SQL proposal has not passed the execution validator.

To answer clarification questions interactively:

```sh
python main.py --generate-sql --interactive
```

After the initial question, the model can ask for a missing team, counting rule,
population, or other detail. Type your answer at `Your answer (or /cancel):`.
The next generation attempt retains the original question and every earlier
clarification/reply pair, so short answers such as "only active athletes" are
interpreted in context. Choices apply to this request only; shared domain rules
and context files are never edited by the exchange.

Each reply uses the same validation as the original question, including the
2,000-character and SQL-smuggling checks. Invalid replies are re-prompted locally
and do not trigger an API call. Three invalid replies cancel the request. There
are at most three clarification replies (four generation attempts in total),
with an 8,000-character cap across the original question and clarification text.
The reference clock stays fixed for the whole request. Model clarifications are
limited to 500 characters, and a denial ends the exchange immediately.

Type `/cancel`, send EOF, or press Ctrl-C to stop. EOF and `/cancel` exit with
status 0; Ctrl-C exits with 130. Exhausting the clarification/invalid-reply budget
exits with status 1. Every final outcome includes `executed: false` and
`usage_totals` across all generation attempts; `usage` is the latest API response.
SDK transport retries follow the SDK's own policy, separately from this round cap.

`--generate-sql --prompt "..."` remains a single-command mode: it returns a
structured `clarify` decision without reading stdin. A fresh invocation starts a
fresh request; previews do not persist a conversation. Application code can use
`SQLClarificationSession.propose()` and `.reply()` to implement its own UI.
Alternatively, `process_request()` now propagates `ClarificationRequiredError`
instead of flattening it into an ordinary answer. Callers can resume it with the
original question, the same `as_of`, and typed `ClarificationTurn` history.

History is sent as alternating assistant clarification/user reply messages,
following [OpenAI's conversation-state guidance](https://developers.openai.com/api/docs/guides/conversation-state).
The original request and stable instructions/cache key remain unchanged. Only
the untrusted conversation suffix grows; replies never become developer instructions.

The checked-in files serve different purposes:

| File | Purpose |
| --- | --- |
| `context/schema.json` | `SHOW TABLES` and stable `SHOW CREATE TABLE` definitions. |
| `context/domain_rules.yaml` | UTC conventions, metric definitions, and unresolved questions. |
| `context/sql_examples.json` | Five question-to-SQL examples, parameters, and explicit interpretations. |
| `prompts/sql_generation.txt` | Query rules, instruction boundaries, and output requirements. |
| `tests/fixtures/survey_data.sql` | Synthetic test data; a portable subset of the real schema. |
| `tests/fixtures/query_cases.json` | Fixed reference time and expected results, excluded from prompts. |
| `tests/fixtures/generation_eval_cases.json` | Model-evaluation cases, including all-time versus seven-day counts; excluded from prompts. |

Refresh the complete schema explicitly after database migrations or changing
the configured database:

```sh
python schema_context.py
```

The snapshot currently has 13 application tables, `_prisma_migrations`, and the
`teamcompliancesummary` view. The `mcp` account cannot read that view's complete
definition. Its visible columns and the permission limitation are recorded; the
SQL prompt excludes it rather than guessing its logic. All available definitions
are included in the prompt. Table names are sorted and the changing table-level
`AUTO_INCREMENT=<counter>` option is removed while the column attribute remains.
Schema comments, constraints, indexes, and other definition text are preserved.
Refresh uses a temporary file and replaces the previous snapshot after collection
succeeds. No schema/data changes are made in MySQL.

The UTC convention is confirmed. Two domain choices are still unconfirmed:
whether team submissions mean Compliance counters or Survey rows for current
members, and whether the athlete population excludes inactive/deactivated people.
The conditional examples demonstrate the counters/active-population interpretation
and label it explicitly. Until those choices are confirmed in the rules file or
specified in the user's question, the model is instructed to ask for clarification.
Survey has no TeamID, so per-team Compliance totals must not be presented as
equivalent to Survey row counts. Athlete names/emails are also absent; "who"
can return AthleteID, not invented names.

`sql_context.py` loads the static context once per process and builds a stable
prefix from instructions, full schema, domain rules, and examples. A SHA-256
content hash supplies a stable `prompt_cache_key`. The question and reference
time follow that prefix in a user message; only the question goes through the
SQL-smuggling input filter. Restart long-running processes or call
`load_sql_context.cache_clear()` after editing context files.

This layout enables eligible prefix reuse on GPT-5 Mini; it does not guarantee a
cache hit or permanent retention. The complete context is still sent on every
generation request. Check `usage.input_tokens_details.cached_tokens` in the
printed API usage to measure actual reuse. See [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
SQL generation caps output at 3,000 tokens; an incomplete response is rejected.
Reference clocks are available bind values, not implicit requested date filters.
Evaluation cases distinguish all-time totals from explicitly requested seven-day
counts; changing the reference clock alone must not narrow the population.

Verify the five developer-reviewed examples against the live database:

```sh
python verify_examples.py --team-id fJ8Xoh --as-of 2026-08-12T00:00:00+00:00
```

Choose an existing TeamID from your database. This script runs only the curated
example queries, never model-generated SQL. It uses a read-only consistent
transaction, a five-second query deadline, and a 100-row result cap. Run
`python verify_examples.py --inspect` for small aggregate diagnostics. Live
results reflect the current development data and are not regression fixtures.

Automated query-logic tests use an isolated in-memory SQLite database, the same
SQL with only `%s` changed to `?`, and synthetic expected results. They exercise
multi-team membership, inactive/deactivated athletes, ties, empty/unknown teams,
and both boundaries of the seven-day window. These portable tests complement
the real MySQL syntax/execution check; they do not claim full MySQL equivalence.
Never load the synthetic fixture into `syp_dev`.

## Execute SQL and log experiments

Run the complete question → SQL → MySQL → plain-English-answer flow:

```sh
python main.py --execute --interactive
```

Or supply a fully specified question in one command:

```sh
python main.py --execute --prompt "How many survey submissions are recorded in total, across all athletes and all time?"
```

`--execute` and `--generate-sql` are mutually exclusive. Both support `--as-of`
for a fixed reference clock. Execution uses the same clarification loop as the
preview: no query runs while clarification is pending. With `--prompt`, a
clarification is printed and the command ends without reading stdin; use
`--interactive` to answer it in the same session.

The terminal prints the user question, model/clarification exchanges, proposed
SQL and bound parameters, actual capped SQL, returned rows, and final English
answer. Each execution-mode run also writes these events immediately to a
timestamped JSONL file in `logs/`. The log path is printed at startup. Logs include
UTC timestamps, ordered stages, model ID, schema/prompt context hash, fixed
reference time, per-response token usage, SQL-generation usage totals, limits,
denials, cancellations, and errors. SQL/results remain available if the final
answer call fails. Credentials, the connection URL, and hidden model reasoning
are not logged. Questions, clarification replies, and returned test data are
logged intentionally. `logs/` is excluded from Git; retain selected runs for the
assignment report. These traces support later experiments; they do not replace
human checks of SQL meaning or answer correctness.

The independent [SQLGlot parser](https://sqlglot.com/sqlglot.html) checks MySQL
syntax before connection and again at execution. Only one SELECT (including
nonrecursive CTEs and unions) over the 13 saved application tables is allowed.
Other schemas, the unavailable view, migration table, writes, file operations,
locks, comments/hints, variables, and functions outside the enabled builtins
are rejected. Values are passed separately to Connector/Python as parameters.
The configured database must match the saved snapshot. Unknown column names
and other runtime SQL errors are reported and logged without automatic repair
or re-execution.

Each query gets a dedicated connection and read-only transaction, with a
[five-second SELECT deadline](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time),
five-second lock waits, and ten-second socket read/write timeouts. The executor
adds or reduces the outer LIMIT to at most 101, fetches at most that many rows,
and returns at most 100 rows and 16 KiB of serialized columns/row data. Aggregate
queries count their full SQL scope; the result-row cap does not sample the rows
being counted. A row larger than the budget produces an explicit error; it is
never silently treated as an empty result. Decimal precision is preserved as
strings, timestamps are serialized in UTC, and binary values use base64.
Duplicate output column names require distinct aliases.

When rows are omitted, or a model-supplied LIMIT may hide more rows, the answer
receives a completeness warning. No rows produces `No matching data found.`
without another model call. A row containing a count of zero still goes through
the answer call. The separate `prompts/answer_generation.txt` directs the model
to ground its answer in the actual results, preserve clarification choices, and
treat database strings as untrusted data. Its output cap is 2,500 tokens;
incomplete, refused, and empty answers are errors. Logging retains the results
even if explanation fails. Model answers still need correctness review.

Application code can call `connector.process_request(question)` for the same
template flow, or `respond_to_query()` to finish a proposal already obtained
through `SQLClarificationSession` without generating it a second time. The CLI
owns experiment logging; direct connector calls do not create log files.

## Assignment alignment

Source: [BYU CS 452 AI + SQL assignment](https://github.com/byu-cs-452/byu-cs-452-class-content/blob/main/aisql/readme.md).

- [x] Finish the question → SQL → database → plain-English-answer pipeline.
- [ ] Verify/export the schema picture.
- [ ] Compare prompting approaches and document observations from the [required paper](https://arxiv.org/abs/2305.11853).
- [ ] Record successful/unsuccessful end-to-end examples and at least six additional cases.
- [ ] Prepare code access, database purpose, schema image, and class post.

Each student must read the paper, create a schema picture, contribute to design,
run with their own generated API key, try their own question, self-report in
Canvas, and review at least three classmates' posts.

Our experiment plan: compare a schema-only zero-shot prompt with the current
same-database few-shot examples. Keep the schema, domain definitions, model,
reference time, and evaluated questions fixed. Use held-out questions rather than
the five prompt demonstrations, and record SQL result correctness, final-answer
correctness, clarification behavior, token usage, and cost. This comparison and
the end-to-end report remain future work; fixture tests alone are not a model
evaluation. The existing ERD PDF still needs checking against the saved schema.

## Implementation roadmap

1. **Input and flow (implemented):** CLI input, shared local validation, and the
   `ModelConnector.process_request()` template method. The template calls abstract
   hooks in order: generate SQL, validate SQL, execute SQL, prepare results, and
   generate an answer. Denials return early and clarification exceptions propagate
   for the UI to collect a reply; zero rows return a fixed message without an
   answer-generation call. Stage failures propagate to the caller.
2. **SQL generation (implemented):** Saved full schema, domain rules, five
   verified conditional examples, structured query/deny/clarify output, stable
   cache prefix/key, aggregate usage reporting, bounded interactive clarification,
   and generation-only CLI. Confirm the two
   remaining metric/population rules before treating the conditional examples as
   default interpretations.
3. **SQL validation and execution (implemented):** Parse and restrict generated SQL, use the
   existing read-only `mcp` user, and enforce query deadlines, row limits, and
   result-size limits. Failures stop the request with a logged error; automatic
   SQL repair/retry is not implemented.
4. **Results and answers (implemented):** Serialize database values, preserve
   completeness metadata, generate grounded plain-English answers, skip the
   answer call for empty results, and record execution experiments. Automated
   tests cover errors, limits, clarification, no data, and full transcript order.
5. **Evaluation (next):** Compare prompt variants on held-out questions and
   review both SQL results and final answers; document successful and failed runs.

The existing `generate_response()` provider smoke test and generation-only
preview remain available alongside the completed execution flow.

Sensitive-data filtering and per-user authorization are out of scope for this
class prototype, which uses junk development data. The read-only database account
does not enforce additional per-user data-access restrictions.

Run connector tests without a running database or API requests:

```sh
python -m unittest discover -s tests -v
```
