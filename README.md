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

Running without `--prompt` only checks MySQL. The model request uses the official
OpenAI SDK's [Responses API](https://developers.openai.com/api/reference/python)
and returns its text output. Each call is an independent prompt; conversation
history and database tools are not attached to the model yet.

To use the provider directly, load the environment before calling the factory:

```python
from pathlib import Path
from dotenv import load_dotenv
from model_provider import create_model_connector

load_dotenv(Path(".env"))
with create_model_connector() as connector:
    print(connector.generate_response("Say hello."))
```

Run connector tests without a running database or API requests:

```sh
python -m unittest discover -s tests -v
```
