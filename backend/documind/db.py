from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from documind.config import settings


@lru_cache
def engine() -> Engine:
    return create_engine(
        settings().database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
