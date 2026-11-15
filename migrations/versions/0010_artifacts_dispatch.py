"""Persist artifact bounds and throttle redispatch of queued jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0010_artifacts_dispatch"
down_revision = "0009_split_reflection"
branch_labels = None
depends_on = None


def upgrade():
    # A direct upgrade from 0004 runs the 0005 document backfill in this transaction.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.add_column("documents", sa.Column("original_size_bytes", sa.BigInteger()))
    op.add_column("document_pages", sa.Column("render_size_bytes", sa.BigInteger()))
    op.add_column("document_pages", sa.Column("render_sha256", sa.String(64)))
    op.add_column("jobs", sa.Column("dispatch_after", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("jobs", "dispatch_after")
    op.drop_column("document_pages", "render_sha256")
    op.drop_column("document_pages", "render_size_bytes")
    op.drop_column("documents", "original_size_bytes")
