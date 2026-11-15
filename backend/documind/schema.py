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


def col(name, type_, *constraints, **kw):
    return sa.Column(name, type_, *constraints, nullable=False, **kw)


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
    sa.Column("original_size_bytes", sa.BigInteger),
    col("object_key", sa.Text),
    col("lifecycle_status", sa.Text, server_default="active"),
    col("latest_ingestion_version", sa.String(64)),
    sa.Column("preview_ingestion_version", sa.String(64)),
    sa.Column("last_published_ingestion_version", sa.String(64)),
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
for pointer in [
    "latest_ingestion_version",
    "preview_ingestion_version",
    "last_published_ingestion_version",
]:
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
    sa.Column("document_id", UUID),
    sa.Column("ingestion_version", sa.String(64)),
    col("kind", sa.Text),
    col("idempotency_key", sa.Text),
    col("status", sa.Text, server_default="queued"),
    col("attempts", sa.Integer, server_default="0"),
    sa.Column("lease_token", UUID),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    timestamp("next_attempt_at"),
    sa.Column("dispatch_after", sa.DateTime(timezone=True)),
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
    sa.CheckConstraint(
        "(kind IN ('ingest','delete') AND document_id IS NOT NULL "
        "AND ingestion_version IS NOT NULL)"
        " OR (kind IN ('publication','query','validate','comparison','report')"
        " AND document_id IS NULL AND ingestion_version IS NULL)",
        name="kind",
    ),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_jobs_workspace"),
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
    sa.Column("render_size_bytes", sa.BigInteger),
    sa.Column("render_sha256", sa.String(64)),
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


def scope_fk(columns, target, name, **options):
    return sa.ForeignKeyConstraint(
        columns, [f"{target}.{column}" for column in columns], name=name, **options
    )


publications = sa.Table(
    "index_publications",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("id", UUID, primary_key=True),
    sa.Column("parent_id", UUID),
    col("status", sa.Text, server_default="building"),
    col("collection", sa.Text),
    sa.Column("manifest", JSONB),
    sa.Column("sparse_key", sa.Text),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    timestamp("created_at"),
    scope_fk(["workspace_id", "id"], "jobs", "fk_publication_job"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "parent_id"],
        ["index_publications.workspace_id", "index_publications.id"],
        name="fk_publication_parent",
    ),
    sa.CheckConstraint("status IN ('building','published','retired','failed')", name="status"),
)
workspaces.append_column(sa.Column("active_publication_id", UUID))
workspaces.append_constraint(
    sa.ForeignKeyConstraint(
        ["id", "active_publication_id"],
        ["index_publications.workspace_id", "index_publications.id"],
        name="fk_workspace_publication",
        use_alter=True,
    )
)
publication_documents = sa.Table(
    "publication_documents",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("publication_id", UUID, primary_key=True),
    col("document_id", UUID, primary_key=True),
    col("ingestion_version", sa.String(64)),
    sa.UniqueConstraint("workspace_id", "publication_id", "document_id", "ingestion_version"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "publication_id"],
        ["index_publications.workspace_id", "index_publications.id"],
        name="fk_member_publication",
    ),
    scope_fk(
        ["workspace_id", "document_id", "ingestion_version"],
        "ingestion_versions",
        "fk_member_version",
    ),
)
chunks = sa.Table(
    "document_chunks",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("id", UUID, primary_key=True),
    col("publication_id", UUID),
    col("document_id", UUID),
    col("ingestion_version", sa.String(64)),
    col("page_id", UUID),
    col("element_id", UUID),
    col("start_char", sa.Integer),
    col("end_char", sa.Integer),
    col("text", sa.Text),
    col("token_count", sa.Integer),
    sa.Column("table_coordinates", JSONB),
    scope_fk(
        ["workspace_id", "publication_id", "document_id", "ingestion_version"],
        "publication_documents",
        "fk_chunk_membership",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "document_id", "ingestion_version", "page_id", "element_id"],
        [
            "document_elements.workspace_id",
            "document_elements.document_id",
            "document_elements.ingestion_version",
            "document_elements.page_id",
            "document_elements.id",
        ],
        name="fk_chunk_element",
    ),
    sa.CheckConstraint("start_char >= 0 AND end_char > start_char", name="span"),
    sa.CheckConstraint("token_count BETWEEN 1 AND 240", name="token_budget"),
)
phase_requests = sa.Table(
    "phase2_request_keys",
    metadata,
    col("workspace_id", UUID, sa.ForeignKey("workspaces.id"), primary_key=True),
    col("principal_id", UUID, primary_key=True),
    col("operation", sa.Text, primary_key=True),
    col("key", sa.Text, primary_key=True),
    col("request_hash", sa.String(64)),
    col("resource_id", UUID),
    sa.Column("job_id", UUID),
    sa.Column("response", JSONB),
    sa.ForeignKeyConstraint(
        ["workspace_id", "job_id"], ["jobs.workspace_id", "jobs.id"], name="fk_phase_request_job"
    ),
)
connections = sa.Table(
    "inference_connections",
    metadata,
    col("workspace_id", UUID, sa.ForeignKey("workspaces.id"), primary_key=True),
    col("id", UUID, primary_key=True),
    col("owner_principal_id", UUID),
    col("display_name", sa.Text),
    col("config_version", sa.Integer),
    col("status", sa.Text, server_default="configured"),
    sa.Column("check_id", UUID),
    sa.Column("error_code", sa.Text),
    timestamp("created_at"),
    sa.CheckConstraint("status IN ('configured','ready','unavailable','revoked')", name="status"),
)
secrets = sa.Table(
    "inference_secrets",
    metadata,
    col("workspace_id", UUID, sa.ForeignKey("workspaces.id"), primary_key=True),
    col("id", UUID, primary_key=True),
    col("ciphertext", sa.LargeBinary),
)
connection_versions = sa.Table(
    "inference_connection_versions",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("connection_id", UUID, primary_key=True),
    col("config_version", sa.Integer, primary_key=True),
    col("endpoint_url", sa.Text),
    col("model_id", sa.Text),
    sa.Column("secret_id", UUID),
    sa.ForeignKeyConstraint(
        ["workspace_id", "connection_id"],
        ["inference_connections.workspace_id", "inference_connections.id"],
        name="fk_config_connection",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "secret_id"],
        ["inference_secrets.workspace_id", "inference_secrets.id"],
        name="fk_config_secret",
    ),
    sa.CheckConstraint("config_version >= 1", name="version"),
)
capability_checks = sa.Table(
    "inference_capability_checks",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("id", UUID, primary_key=True),
    col("connection_id", UUID),
    col("config_version", sa.Integer),
    sa.Column("result", JSONB),
    scope_fk(["workspace_id", "id"], "jobs", "fk_capability_job"),
    scope_fk(
        ["workspace_id", "connection_id", "config_version"],
        "inference_connection_versions",
        "fk_capability_version",
    ),
    sa.UniqueConstraint("workspace_id", "connection_id", "config_version", "id"),
)
connections.append_constraint(
    sa.ForeignKeyConstraint(
        ["workspace_id", "id", "config_version", "check_id"],
        [
            "inference_capability_checks.workspace_id",
            "inference_capability_checks.connection_id",
            "inference_capability_checks.config_version",
            "inference_capability_checks.id",
        ],
        name="fk_connection_current_check",
        use_alter=True,
    )
)
query_runs = sa.Table(
    "query_runs",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("id", UUID, primary_key=True),
    col("principal_id", UUID),
    sa.Column("publication_id", UUID),
    col("mode", sa.Text, server_default="baseline_rag"),
    sa.Column("runtime_snapshot", JSONB(none_as_null=True)),
    sa.Column("runtime_progress", JSONB(none_as_null=True)),
    sa.Column("question", sa.Text),
    sa.Column("connection_id", UUID),
    sa.Column("config_version", sa.Integer),
    sa.Column("check_id", UUID),
    col("deadline", sa.DateTime(timezone=True)),
    col("generation_started", sa.Boolean, server_default=sa.false()),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.Column("result", JSONB(none_as_null=True)),
    scope_fk(["workspace_id", "id"], "jobs", "fk_query_job"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "publication_id"],
        ["index_publications.workspace_id", "index_publications.id"],
        name="fk_query_publication",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "connection_id", "config_version", "check_id"],
        [
            "inference_capability_checks.workspace_id",
            "inference_capability_checks.connection_id",
            "inference_capability_checks.config_version",
            "inference_capability_checks.id",
        ],
        name="fk_query_capability",
    ),
    sa.CheckConstraint(
        "num_nonnulls(connection_id, config_version, check_id) IN (0, 3)", name="connection_scope"
    ),
    sa.CheckConstraint(
        "(mode = 'baseline_rag' AND publication_id IS NOT NULL AND runtime_snapshot IS NULL)"
        " OR (mode = 'self_rag' AND runtime_snapshot IS NOT NULL"
        " AND jsonb_typeof(runtime_snapshot) = 'object')",
        name="runtime_scope",
    ),
    sa.CheckConstraint(
        "runtime_progress IS NULL OR "
        "(mode = 'self_rag' AND jsonb_typeof(runtime_progress) = 'object')",
        name="runtime_progress_scope",
    ),
    sa.UniqueConstraint("workspace_id", "id", "publication_id"),
)
query_documents = sa.Table(
    "query_documents",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("query_id", UUID, primary_key=True),
    col("document_id", UUID, primary_key=True),
    col("publication_id", UUID),
    col("ingestion_version", sa.String(64)),
    sa.ForeignKeyConstraint(
        ["workspace_id", "query_id", "publication_id"],
        ["query_runs.workspace_id", "query_runs.id", "query_runs.publication_id"],
        name="fk_query_docs_run",
    ),
    scope_fk(
        ["workspace_id", "publication_id", "document_id", "ingestion_version"],
        "publication_documents",
        "fk_query_docs_member",
    ),
)
query_events = sa.Table(
    "query_events",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("query_id", UUID, primary_key=True),
    col("seq", sa.Integer, primary_key=True),
    col("event_type", sa.Text),
    col("payload", JSONB),
    timestamp("occurred_at"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "query_id"],
        ["query_runs.workspace_id", "query_runs.id"],
        name="fk_event_query",
    ),
)
sa.Index(
    "uq_query_terminal",
    query_events.c.workspace_id,
    query_events.c.query_id,
    unique=True,
    postgresql_where=query_events.c.event_type.in_(("answer", "error")),
)

citations = sa.Table(
    "citations",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("id", UUID, primary_key=True),
    col("query_id", UUID),
    col("chunk_id", UUID),
    col("start_char", sa.Integer),
    col("end_char", sa.Integer),
    col("quote", sa.Text),
    col("claim_start", sa.Integer),
    col("claim_end", sa.Integer),
    col("verifier_version", sa.Text),
    sa.ForeignKeyConstraint(
        ["workspace_id", "query_id"],
        ["query_runs.workspace_id", "query_runs.id"],
        name="fk_citation_query",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "chunk_id"],
        ["document_chunks.workspace_id", "document_chunks.id"],
        name="fk_citation_chunk",
    ),
    sa.CheckConstraint("start_char >= 0 AND end_char > start_char", name="source_span"),
    sa.CheckConstraint("claim_start >= 0 AND claim_end > claim_start", name="claim_span"),
)

investigations = sa.Table(
    "investigations",
    metadata,
    col("workspace_id", UUID, sa.ForeignKey("workspaces.id"), primary_key=True),
    col("id", UUID, primary_key=True),
    sa.Column("job_id", UUID),
    sa.Column("name", sa.Text),
    col("rule_version", sa.Text),
    timestamp("created_at"),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("workspace_id", "job_id"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "job_id"],
        ["jobs.workspace_id", "jobs.id"],
        name="fk_investigation_job",
    ),
    sa.CheckConstraint("(revoked_at IS NULL) = (name IS NOT NULL)", name="name_visibility"),
)

investigation_documents = sa.Table(
    "investigation_documents",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("investigation_id", UUID, primary_key=True),
    col("document_id", UUID, primary_key=True),
    col("ingestion_version", sa.String(64)),
    sa.UniqueConstraint("workspace_id", "investigation_id", "document_id", "ingestion_version"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "investigation_id"],
        ["investigations.workspace_id", "investigations.id"],
        name="fk_investigation_source",
    ),
    scope_fk(
        ["workspace_id", "document_id", "ingestion_version"],
        "ingestion_versions",
        "fk_investigation_version",
    ),
)
sa.Index(
    "ix_investigation_source_document",
    investigation_documents.c.workspace_id,
    investigation_documents.c.document_id,
)

extracted_records = sa.Table(
    "extracted_records",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("investigation_id", UUID, primary_key=True),
    col("document_id", UUID, primary_key=True),
    col("ingestion_version", sa.String(64)),
    sa.Column("kind", sa.Text),
    sa.Column("invoice_number", sa.String(64)),
    sa.Column("po_number", sa.String(64)),
    sa.Column("vendor_id", sa.String(64)),
    sa.Column("currency", sa.String(3)),
    sa.Column("total_basis", sa.Text),
    sa.Column("grand_total", sa.Numeric(20, 6)),
    sa.Column("issue", sa.Text),
    sa.UniqueConstraint("workspace_id", "investigation_id", "document_id", "ingestion_version"),
    scope_fk(
        ["workspace_id", "investigation_id", "document_id", "ingestion_version"],
        "investigation_documents",
        "fk_record_investigation_source",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("kind IN ('invoice', 'purchase_order')", name="kind"),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    sa.CheckConstraint("total_basis IN ('tax-inclusive', 'tax-exclusive')", name="basis"),
    sa.CheckConstraint(
        "abs(grand_total) <= 99999999999999.999999 AND currency IS NOT NULL OR grand_total IS NULL",
        name="finite_money",
    ),
)

record_evidence = sa.Table(
    "record_evidence",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("investigation_id", UUID, primary_key=True),
    col("document_id", UUID, primary_key=True),
    col("field", sa.Text, primary_key=True),
    col("ingestion_version", sa.String(64)),
    col("page_id", UUID),
    col("element_id", UUID),
    col("start_char", sa.Integer),
    col("end_char", sa.Integer),
    col("quote", sa.Text),
    scope_fk(
        ["workspace_id", "investigation_id", "document_id", "ingestion_version"],
        "extracted_records",
        "fk_comparison_evidence_record",
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "document_id", "ingestion_version", "page_id", "element_id"],
        [
            "document_elements.workspace_id",
            "document_elements.document_id",
            "document_elements.ingestion_version",
            "document_elements.page_id",
            "document_elements.id",
        ],
        name="fk_comparison_evidence_element",
    ),
    sa.CheckConstraint("start_char >= 0 AND end_char > start_char", name="span"),
    sa.CheckConstraint(
        "field IN ('kind','invoice_number','po_number','vendor_id','currency','total_basis',"
        "'grand_total')",
        name="field",
    ),
)

findings = sa.Table(
    "findings",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("investigation_id", UUID, primary_key=True),
    col("type", sa.Text),
    sa.Column("reason", sa.Text),
    sa.Column("difference", sa.Numeric(21, 6)),
    sa.ForeignKeyConstraint(
        ["workspace_id", "investigation_id"],
        ["investigations.workspace_id", "investigations.id"],
        name="fk_finding_investigation",
    ),
    sa.CheckConstraint(
        "(type = 'AMOUNT_MATCH' AND difference = 0 OR type = 'AMOUNT_MISMATCH' AND difference <> 0)"
        " AND difference IS NOT NULL AND abs(difference) <= 199999999999999.999998"
        " AND reason IS NULL OR type IN ('UNVERIFIED_MATCH','UNVERIFIED_TOTAL')"
        " AND difference IS NULL AND reason IS NOT NULL",
        name="outcome",
    ),
)

finding_evidence = sa.Table(
    "finding_evidence",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("investigation_id", UUID, primary_key=True),
    col("role", sa.Text, primary_key=True),
    col("document_id", UUID),
    col("field", sa.Text, server_default="grand_total"),
    scope_fk(
        ["workspace_id", "investigation_id"],
        "findings",
        "fk_pair_finding",
        ondelete="CASCADE",
    ),
    scope_fk(
        ["workspace_id", "investigation_id", "document_id", "field"],
        "record_evidence",
        "fk_pair_record_evidence",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint(
        "role IN ('invoice','purchase_order') AND field = 'grand_total'", name="role"
    ),
)

report_artifacts = sa.Table(
    "report_artifacts",
    metadata,
    col("workspace_id", UUID, primary_key=True),
    col("id", UUID, primary_key=True),
    col("investigation_id", UUID),
    col("format", sa.Text, server_default="csv"),
    timestamp("created_at"),
    sa.Column("content", sa.LargeBinary),
    sa.Column("sha256", sa.String(64)),
    scope_fk(["workspace_id", "id"], "jobs", "fk_comparison_report_job"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "investigation_id"],
        ["investigations.workspace_id", "investigations.id"],
        name="fk_report_investigation",
    ),
    sa.UniqueConstraint("workspace_id", "investigation_id", "format"),
    sa.CheckConstraint("format = 'csv'", name="format"),
    sa.CheckConstraint("(content IS NULL) = (sha256 IS NULL)", name="content_hash"),
)
