"""Keep private recovery checkpoints only while reflection queries are active."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_reflection_recovery"
down_revision = "0007_reflection"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("query_runs", sa.Column("runtime_progress", JSONB()))
    op.create_check_constraint(
        "ck_query_runs_runtime_progress_scope",
        "query_runs",
        "runtime_progress IS NULL OR "
        "(mode = 'self_rag' AND jsonb_typeof(runtime_progress) = 'object')",
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM query_runs WHERE runtime_progress IS NOT NULL) THEN
                RAISE EXCEPTION 'Reflection recovery state exists; finish active runs first';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_query_runs_runtime_progress_scope", "query_runs", type_="check")
    op.drop_column("query_runs", "runtime_progress")
