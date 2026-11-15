"""Pin the authorized reflection runtime and allow unscoped general queries."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_reflection"
down_revision = "0006_comparisons"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("query_runs", "publication_id", nullable=True)
    op.add_column(
        "query_runs", sa.Column("mode", sa.Text(), server_default="baseline_rag", nullable=False)
    )
    op.add_column("query_runs", sa.Column("runtime_snapshot", JSONB()))
    op.create_check_constraint(
        "ck_query_runs_runtime_scope",
        "query_runs",
        "(mode = 'baseline_rag' AND publication_id IS NOT NULL AND runtime_snapshot IS NULL)"
        " OR (mode = 'self_rag' AND connection_id IS NULL AND runtime_snapshot IS NOT NULL"
        " AND jsonb_typeof(runtime_snapshot) = 'object')",
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM query_runs WHERE mode = 'self_rag') THEN
                RAISE EXCEPTION 'Reflection query data exists; restore a pre-upgrade backup';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_query_runs_runtime_scope", "query_runs", type_="check")
    op.drop_column("query_runs", "runtime_snapshot")
    op.drop_column("query_runs", "mode")
    op.alter_column("query_runs", "publication_id", nullable=False)
