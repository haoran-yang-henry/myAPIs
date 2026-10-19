import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo


@pytest.fixture
def database_url():
    """A fresh, empty schema per test in the database at TEST_DATABASE_URL.

    Locally: `docker compose up -d postgres` and
    TEST_DATABASE_URL=postgresql://knowledge:knowledge@localhost:5434/knowledge
    """
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("set TEST_DATABASE_URL to a Postgres with pgvector to run database tests")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"CREATE SCHEMA {schema}")
    yield make_conninfo(base, options=f"-c search_path={schema},public")
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
