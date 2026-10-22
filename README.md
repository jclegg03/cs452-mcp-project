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
and returns its text output. Each call is an independent prompt; conversation
history and database tools are not attached to the model yet.

Alternatively, collect one question from the terminal:

```sh
python main.py --interactive
```

`--prompt` and `--interactive` are mutually exclusive. Both currently run the
provider smoke test; the SQL workflow is scaffolded but not connected to the CLI
until SQL generation and execution are implemented. Interactive mode reads one
line. EOF exits without a request; Ctrl-C exits with status 130.

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
encoded SQL can evade it. It does not replace the planned generated-SQL validator
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

## Implementation roadmap

1. **Input and flow (implemented):** CLI input, shared local validation, and the
   `ModelConnector.process_request()` template method. The template calls abstract
   hooks in order: generate SQL, validate SQL, execute SQL, prepare results, and
   generate an answer. Denials and clarification requests return early; zero
   rows return a fixed message without an answer-generation call. Stage failures
   propagate to the caller.
2. **SQL generation:** Agree on schema context and trusted instructions, then
   implement structured query/deny/clarify outcomes. Keep user text separate from
   instructions and constrain unrelated requests and prompt-injection attempts.
3. **SQL validation and execution:** Parse and restrict generated SQL, use the
   existing read-only `mcp` user, and enforce query deadlines, row limits, and
   result-size limits. Add bounded error recovery and clear failure messages.
4. **Results and answers:** Serialize database values, preserve truncation
   metadata, and implement grounded plain-English answers. Add model output and
   retry budgets, usage tracking, and end-to-end cases for errors and no data.

The OpenAI SQL hooks currently raise `NotImplementedError` before an API request
or SQL execution. This deliberately prevents the incomplete workflow from
treating ordinary model output as executable SQL. The existing
`generate_response()` provider test remains available.

Sensitive-data filtering and per-user authorization are out of scope for this
class prototype, which uses junk development data. The read-only database account
does not enforce additional per-user data-access restrictions.

Run connector tests without a running database or API requests:

```sh
python -m unittest discover -s tests -v
```
