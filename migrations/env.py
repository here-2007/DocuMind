from alembic import context
from documind.db import engine
from documind.schema import metadata

if context.is_offline_mode():
    raise RuntimeError("Use an explicit online local database for migrations")
with engine().connect() as connection:
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
