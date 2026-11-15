"""Keep the last published source version across snapshot revocation."""

import sqlalchemy as sa
from alembic import op

revision = "0005_publication_recovery"
down_revision = "0004_phase2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("last_published_ingestion_version", sa.String(64)))
    op.create_foreign_key(
        "fk_documents_last_published_ingestion_version",
        "documents",
        "ingestion_versions",
        ["workspace_id", "id", "last_published_ingestion_version"],
        ["workspace_id", "document_id", "ingestion_version"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.execute("""
        UPDATE documents AS d
        SET last_published_ingestion_version = previous.ingestion_version
        FROM (
            SELECT DISTINCT ON (m.workspace_id, m.document_id)
                m.workspace_id, m.document_id, m.ingestion_version
            FROM publication_documents AS m
            JOIN jobs AS j ON j.workspace_id = m.workspace_id AND j.id = m.publication_id
            WHERE j.kind = 'publication' AND j.status = 'succeeded'
            ORDER BY m.workspace_id, m.document_id, j.finished_at DESC, j.id DESC
        ) AS previous
        WHERE d.workspace_id = previous.workspace_id AND d.id = previous.document_id
            AND d.lifecycle_status = 'active'
    """)


def downgrade():
    op.drop_constraint(
        "fk_documents_last_published_ingestion_version", "documents", type_="foreignkey"
    )
    op.drop_column("documents", "last_published_ingestion_version")
