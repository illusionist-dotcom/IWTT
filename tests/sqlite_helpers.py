"""SQLite test helpers with deterministic connection cleanup on Windows."""
from contextlib import contextmanager
import sqlite3
from typing import Iterator


@contextmanager
def connect_sqlite(database: str) -> Iterator[sqlite3.Connection]:
    """Commit or roll back a connection context, then always close the handle."""
    connection = sqlite3.connect(database)
    try:
        with connection:
            yield connection
    finally:
        connection.close()
