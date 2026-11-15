"""Allow an independently pinned generator on a trained reflection query."""

from alembic import op

revision = "0009_split_reflection"
down_revision = "0008_reflection_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_query_runs_runtime_scope", "query_runs", type_="check")
    op.create_check_constraint(
        "ck_query_runs_runtime_scope",
        "query_runs",
        "(mode = 'baseline_rag' AND publication_id IS NOT NULL AND runtime_snapshot IS NULL)"
        " OR (mode = 'self_rag' AND runtime_snapshot IS NOT NULL"
        " AND jsonb_typeof(runtime_snapshot) = 'object')",
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM query_runs
                       WHERE mode = 'self_rag' AND connection_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Split reflection queries exist; restore a pre-upgrade backup';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_query_runs_runtime_scope", "query_runs", type_="check")
    op.create_check_constraint(
        "ck_query_runs_runtime_scope",
        "query_runs",
        "(mode = 'baseline_rag' AND publication_id IS NOT NULL AND runtime_snapshot IS NULL)"
        " OR (mode = 'self_rag' AND connection_id IS NULL AND runtime_snapshot IS NOT NULL"
        " AND jsonb_typeof(runtime_snapshot) = 'object')",
    )
