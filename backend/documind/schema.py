"""PDF extraction and provenance tables. Alembic owns schema creation."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData(
    naming_convention={
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
    }
)


def col(name, type_, **kw):
    return sa.Column(name, type_, nullable=False, **kw)


def timestamp(name):
    return col(name, sa.DateTime(timezone=True), server_default=sa.func.now())


workspaces = sa.Table(
    "workspaces",
    metadata,
    col("id", UUID, primary_key=True),
    col("name", sa.Text),
    timestamp("created_at"),
)
documents = sa.Table(
    "documents",
    metadata,
    col("id", UUID, primary_key=True),
    col("workspace_id", UUID),
    col("filename", sa.Text),
    col("media_type", sa.Text),
    col("sha256", sa.String(64)),
    col("object_key", sa.Text),
    col("lifecycle_status", sa.Text, server_default="active"),
    col("latest_ingestion_version", sa.String(64)),
    sa.Column("preview_ingestion_version", sa.String(64)),
    timestamp("created_at"),
    sa.UniqueConstraint("workspace_id", "id"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
    sa.CheckConstraint("lifecycle_status IN ('active','deleting','deleted')", name="lifecycle"),
)
versions = sa.Table(
    "ingestion_versions",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("document_id", UUID, primary_key=True),
    col("ingestion_version", sa.String(64), primary_key=True),
    col("parser_version", sa.Text),
    col("config_hash", sa.String(64)),
    col("config_json", JSONB),
    col("extraction_status", sa.Text, server_default="queued"),
    col("index_status", sa.Text, server_default="not_started"),
    sa.Column("error_code", sa.Text),
    sa.Column("output_hash", sa.String(64)),
    sa.ForeignKeyConstraint(
        ["workspace_id", "document_id"], ["documents.workspace_id", "documents.id"]
    ),
    sa.CheckConstraint(
        "extraction_status IN ('queued','processing','complete','needs_review','failed')",
        name="extraction",
    ),
    sa.CheckConstraint("index_status = 'not_started'", name="no_index_yet"),
)
for pointer in ["latest_ingestion_version", "preview_ingestion_version"]:
    documents.append_constraint(
        sa.ForeignKeyConstraint(
            ["workspace_id", "id", pointer],
            [
                "ingestion_versions.workspace_id",
                "ingestion_versions.document_id",
                "ingestion_versions.ingestion_version",
            ],
            name=f"fk_documents_{pointer}",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        )
    )
jobs = sa.Table(
    "jobs",
    metadata,
    col("id", UUID, primary_key=True),
    col("workspace_id", UUID),
    col("document_id", UUID),
    col("ingestion_version", sa.String(64)),
    col("kind", sa.Text),
    col("idempotency_key", sa.Text),
    col("status", sa.Text, server_default="queued"),
    col("attempts", sa.Integer, server_default="0"),
    sa.Column("lease_token", UUID),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    timestamp("next_attempt_at"),
    sa.Column("error_code", sa.Text),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    timestamp("created_at"),
    sa.UniqueConstraint("workspace_id", "id"),
    sa.UniqueConstraint("workspace_id", "kind", "idempotency_key"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "document_id", "ingestion_version"],
        [
            "ingestion_versions.workspace_id",
            "ingestion_versions.document_id",
            "ingestion_versions.ingestion_version",
        ],
    ),
    sa.CheckConstraint(
        "status IN ('queued','running','succeeded','failed','cancelled')", name="status"
    ),
    sa.CheckConstraint("kind IN ('ingest','delete')", name="kind"),
    sa.CheckConstraint("attempts >= 0", name="attempts"),
)
sa.Index("ix_jobs_dispatch", jobs.c.status, jobs.c.next_attempt_at)
request_keys = sa.Table(
    "request_keys",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("principal_id", UUID, primary_key=True),
    col("operation", sa.Text, primary_key=True),
    col("key", sa.Text, primary_key=True),
    col("request_hash", sa.String(64)),
    col("resource_id", UUID),
    col("job_id", UUID),
    sa.ForeignKeyConstraint(
        ["workspace_id", "resource_id"], ["documents.workspace_id", "documents.id"]
    ),
    sa.ForeignKeyConstraint(["workspace_id", "job_id"], ["jobs.workspace_id", "jobs.id"]),
)
pages = sa.Table(
    "document_pages",
    metadata,
    col("id", UUID, primary_key=True),
    col("workspace_id", UUID),
    col("document_id", UUID),
    col("ingestion_version", sa.String(64)),
    col("page_number", sa.Integer),
    col("width", sa.Float),
    col("height", sa.Float),
    col("rotation", sa.Integer),
    col("render_object_key", sa.Text),
    col("text_raw", sa.Text),
    col("processing_status", sa.Text),
    col("metadata_json", JSONB, server_default=sa.text("'{}'::jsonb")),
    sa.UniqueConstraint("workspace_id", "document_id", "ingestion_version", "page_number"),
    sa.UniqueConstraint("workspace_id", "document_id", "ingestion_version", "id"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "document_id", "ingestion_version"],
        [
            "ingestion_versions.workspace_id",
            "ingestion_versions.document_id",
            "ingestion_versions.ingestion_version",
        ],
    ),
    sa.CheckConstraint("page_number >= 1 AND width > 0 AND height > 0", name="geometry"),
)
elements = sa.Table(
    "document_elements",
    metadata,
    col("id", UUID, primary_key=True),
    col("workspace_id", UUID),
    col("document_id", UUID),
    col("ingestion_version", sa.String(64)),
    col("page_id", UUID),
    col("element_type", sa.Text),
    col("text_raw", sa.Text),
    col("text_normalized", sa.Text),
    col("bbox_norm", JSONB),
    col("reading_order", sa.Integer),
    col("source_type", sa.Text),
    sa.Column("confidence", sa.Float),
    sa.Column("parent_element_id", UUID),
    sa.Column("table_coordinates", JSONB),
    col("metadata_json", JSONB, server_default=sa.text("'{}'::jsonb")),
    col("start_char", sa.Integer),
    col("end_char", sa.Integer),
    sa.UniqueConstraint(
        "workspace_id",
        "document_id",
        "ingestion_version",
        "page_id",
        "id",
        name="uq_element_scoped_parent",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "document_id", "ingestion_version", "page_id", "parent_element_id"],
        [
            "document_elements.workspace_id",
            "document_elements.document_id",
            "document_elements.ingestion_version",
            "document_elements.page_id",
            "document_elements.id",
        ],
        name="fk_element_table_parent",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "document_id", "ingestion_version", "page_id"],
        [
            "document_pages.workspace_id",
            "document_pages.document_id",
            "document_pages.ingestion_version",
            "document_pages.id",
        ],
    ),
    sa.CheckConstraint(
        "reading_order >= 0 AND start_char >= 0 AND end_char >= start_char "
        "AND (end_char > start_char OR element_type = 'table_cell') "
        "AND end_char - start_char = char_length(text_raw)",
        name="span",
    ),
    sa.CheckConstraint(
        "jsonb_array_length(bbox_norm) = 4 "
        "AND (bbox_norm->>0)::float >= 0 AND (bbox_norm->>1)::float >= 0 "
        "AND (bbox_norm->>2)::float <= 1 AND (bbox_norm->>3)::float <= 1 "
        "AND (bbox_norm->>0)::float <= (bbox_norm->>2)::float "
        "AND (bbox_norm->>1)::float <= (bbox_norm->>3)::float",
        name="bbox",
    ),
    sa.CheckConstraint(
        "element_type IN ('paragraph','table','table_cell') "
        "AND source_type IN ('native_text','ocr','manual_correction') "
        "AND (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))",
        name="extraction_identity",
    ),
    sa.CheckConstraint(
        "(element_type = 'table_cell' AND parent_element_id IS NOT NULL "
        "AND table_coordinates IS NOT NULL "
        "AND jsonb_typeof(table_coordinates) = 'object' "
        "AND table_coordinates ?& ARRAY['row_index','column_index',"
        "'row_span','column_span','is_header'] "
        "AND jsonb_typeof(table_coordinates->'row_index') = 'number' "
        "AND jsonb_typeof(table_coordinates->'column_index') = 'number' "
        "AND jsonb_typeof(table_coordinates->'row_span') = 'number' "
        "AND jsonb_typeof(table_coordinates->'column_span') = 'number' "
        "AND jsonb_typeof(table_coordinates->'is_header') IN ('boolean','null') "
        "AND (table_coordinates->>'row_index')::int >= 0 "
        "AND (table_coordinates->>'column_index')::int >= 0 "
        "AND (table_coordinates->>'row_span')::int >= 1 "
        "AND (table_coordinates->>'column_span')::int >= 1) "
        "OR (element_type <> 'table_cell' AND parent_element_id IS NULL "
        "AND table_coordinates IS NULL)",
        name="table_structure",
    ),
)
