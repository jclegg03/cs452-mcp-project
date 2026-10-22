# Local MySQL connection

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

Run connector tests without a running database:

```sh
python -m unittest discover -s tests -v
```
