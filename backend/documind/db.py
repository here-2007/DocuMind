import hashlib
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from documind.config import settings
from documind.errors import AppError


@lru_cache
def engine() -> Engine:
    return create_engine(
        settings().database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


@lru_cache
def schema_heads():
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    return set(ScriptDirectory.from_config(config).get_heads())


def require_current_schema(database):
    with database.connect() as c:
        current = (
            set(c.scalars(text("SELECT version_num FROM alembic_version")))
            if inspect(c).has_table("alembic_version")
            else set()
        )
    if current != schema_heads():
        raise AppError("DATABASE_MIGRATION_REQUIRED", 503)


@contextmanager
def artifact_lock(database, identifier):
    # Session lock spans object/index IO without holding a long SQL transaction.
    key = int.from_bytes(hashlib.sha256(identifier.bytes).digest()[:8], "big", signed=True)
    with database.connect() as c:
        if not c.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            raise AppError("PUBLICATION_BUSY", 409)
        c.commit()
        try:
            yield
        finally:
            c.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            c.commit()
