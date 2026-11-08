"""Frozen Phase 1A PostgreSQL schema."""

from alembic import op

revision = "0001_native"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE workspaces (
	id UUID NOT NULL, 
	name TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_workspaces PRIMARY KEY (id)
)

""")
    op.execute("""
CREATE TABLE documents (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	filename TEXT NOT NULL, 
	media_type TEXT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	object_key TEXT NOT NULL, 
	lifecycle_status TEXT DEFAULT 'active' NOT NULL, 
	latest_ingestion_version VARCHAR(64) NOT NULL, 
	preview_ingestion_version VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_documents PRIMARY KEY (id), 
	CONSTRAINT uq_documents_workspace_id_id UNIQUE (workspace_id, id), 
	CONSTRAINT fk_documents_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id), 
	CONSTRAINT ck_documents_lifecycle CHECK (lifecycle_status IN ('active','deleting','deleted'))
)

""")
    op.execute("""
CREATE TABLE ingestion_versions (
	workspace_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	ingestion_version VARCHAR(64) NOT NULL, 
	parser_version TEXT NOT NULL, 
	config_hash VARCHAR(64) NOT NULL, 
	config_json JSONB NOT NULL, 
	extraction_status TEXT DEFAULT 'queued' NOT NULL, 
	index_status TEXT DEFAULT 'not_started' NOT NULL, 
	error_code TEXT, 
	output_hash VARCHAR(64), 
	CONSTRAINT pk_ingestion_versions PRIMARY KEY (workspace_id, document_id, ingestion_version), 
	CONSTRAINT fk_ingestion_versions_workspace_id_documents FOREIGN KEY(workspace_id, document_id) REFERENCES documents (workspace_id, id), 
	CONSTRAINT ck_ingestion_versions_extraction CHECK (extraction_status IN ('queued','processing','complete','needs_review','failed')), 
	CONSTRAINT ck_ingestion_versions_no_index_yet CHECK (index_status = 'not_started')
)

""")
    op.execute("""
CREATE TABLE document_pages (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	ingestion_version VARCHAR(64) NOT NULL, 
	page_number INTEGER NOT NULL, 
	width FLOAT NOT NULL, 
	height FLOAT NOT NULL, 
	rotation INTEGER NOT NULL, 
	render_object_key TEXT NOT NULL, 
	text_raw TEXT NOT NULL, 
	processing_status TEXT NOT NULL, 
	CONSTRAINT pk_document_pages PRIMARY KEY (id), 
	CONSTRAINT uq_document_pages_workspace_id_document_id_ingestion_ve_ed86 UNIQUE (workspace_id, document_id, ingestion_version, page_number), 
	CONSTRAINT uq_document_pages_workspace_id_document_id_ingestion_version_id UNIQUE (workspace_id, document_id, ingestion_version, id), 
	CONSTRAINT fk_document_pages_workspace_id_ingestion_versions FOREIGN KEY(workspace_id, document_id, ingestion_version) REFERENCES ingestion_versions (workspace_id, document_id, ingestion_version), 
	CONSTRAINT ck_document_pages_geometry CHECK (page_number >= 1 AND width > 0 AND height > 0)
)

""")
    op.execute("""
CREATE TABLE jobs (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	ingestion_version VARCHAR(64) NOT NULL, 
	kind TEXT NOT NULL, 
	idempotency_key TEXT NOT NULL, 
	status TEXT DEFAULT 'queued' NOT NULL, 
	attempts INTEGER DEFAULT '0' NOT NULL, 
	lease_token UUID, 
	lease_expires_at TIMESTAMP WITH TIME ZONE, 
	next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	error_code TEXT, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_jobs PRIMARY KEY (id), 
	CONSTRAINT uq_jobs_workspace_id_id UNIQUE (workspace_id, id), 
	CONSTRAINT uq_jobs_workspace_id_kind_idempotency_key UNIQUE (workspace_id, kind, idempotency_key), 
	CONSTRAINT fk_jobs_workspace_id_ingestion_versions FOREIGN KEY(workspace_id, document_id, ingestion_version) REFERENCES ingestion_versions (workspace_id, document_id, ingestion_version), 
	CONSTRAINT ck_jobs_status CHECK (status IN ('queued','running','succeeded','failed','cancelled')), 
	CONSTRAINT ck_jobs_kind CHECK (kind IN ('ingest','delete')), 
	CONSTRAINT ck_jobs_attempts CHECK (attempts >= 0)
)

""")
    op.execute("""CREATE INDEX ix_jobs_dispatch ON jobs (status, next_attempt_at)""")
    op.execute("""
CREATE TABLE document_elements (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	ingestion_version VARCHAR(64) NOT NULL, 
	page_id UUID NOT NULL, 
	element_type TEXT NOT NULL, 
	text_raw TEXT NOT NULL, 
	text_normalized TEXT NOT NULL, 
	bbox_norm JSONB NOT NULL, 
	reading_order INTEGER NOT NULL, 
	source_type TEXT NOT NULL, 
	confidence FLOAT, 
	start_char INTEGER NOT NULL, 
	end_char INTEGER NOT NULL, 
	CONSTRAINT pk_document_elements PRIMARY KEY (id), 
	CONSTRAINT fk_document_elements_workspace_id_document_pages FOREIGN KEY(workspace_id, document_id, ingestion_version, page_id) REFERENCES document_pages (workspace_id, document_id, ingestion_version, id), 
	CONSTRAINT ck_document_elements_span CHECK (reading_order >= 0 AND start_char >= 0 AND end_char > start_char AND end_char - start_char = char_length(text_raw)), 
	CONSTRAINT ck_document_elements_bbox CHECK (jsonb_array_length(bbox_norm) = 4 AND (bbox_norm->>0)::float >= 0 AND (bbox_norm->>1)::float >= 0 AND (bbox_norm->>2)::float <= 1 AND (bbox_norm->>3)::float <= 1 AND (bbox_norm->>0)::float <= (bbox_norm->>2)::float AND (bbox_norm->>1)::float <= (bbox_norm->>3)::float)
)

""")
    op.execute("""
CREATE TABLE request_keys (
	workspace_id UUID NOT NULL, 
	principal_id UUID NOT NULL, 
	operation TEXT NOT NULL, 
	key TEXT NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	resource_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	CONSTRAINT pk_request_keys PRIMARY KEY (workspace_id, principal_id, operation, key), 
	CONSTRAINT fk_request_keys_workspace_id_documents FOREIGN KEY(workspace_id, resource_id) REFERENCES documents (workspace_id, id), 
	CONSTRAINT fk_request_keys_workspace_id_jobs FOREIGN KEY(workspace_id, job_id) REFERENCES jobs (workspace_id, id)
)

""")
    op.execute(
        """ALTER TABLE documents ADD CONSTRAINT fk_documents_preview_ingestion_version FOREIGN KEY(workspace_id, id, preview_ingestion_version) REFERENCES ingestion_versions (workspace_id, document_id, ingestion_version) DEFERRABLE INITIALLY DEFERRED"""
    )
    op.execute(
        """ALTER TABLE documents ADD CONSTRAINT fk_documents_latest_ingestion_version FOREIGN KEY(workspace_id, id, latest_ingestion_version) REFERENCES ingestion_versions (workspace_id, document_id, ingestion_version) DEFERRABLE INITIALLY DEFERRED"""
    )
    op.execute("""
    CREATE FUNCTION protect_source() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE state text; life text;
    BEGIN
      SELECT v.extraction_status, d.lifecycle_status INTO state, life
      FROM ingestion_versions v JOIN documents d
        ON d.id = v.document_id AND d.workspace_id = v.workspace_id
      WHERE v.workspace_id = OLD.workspace_id AND v.document_id = OLD.document_id
        AND v.ingestion_version = OLD.ingestion_version;
      IF TG_OP = 'DELETE' AND life IN ('deleting','deleted') THEN RETURN OLD; END IF;
      IF state IN ('complete','needs_review') THEN
        RAISE EXCEPTION 'Promoted source is immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_page BEFORE UPDATE OR DELETE ON document_pages
      FOR EACH ROW EXECUTE FUNCTION protect_source();
    CREATE TRIGGER immutable_element BEFORE UPDATE OR DELETE ON document_elements
      FOR EACH ROW EXECUTE FUNCTION protect_source();
    """)
    op.execute("""
    CREATE FUNCTION check_source_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE state text; page_text text;
    BEGIN
      SELECT extraction_status INTO state FROM ingestion_versions
      WHERE workspace_id = NEW.workspace_id AND document_id = NEW.document_id
        AND ingestion_version = NEW.ingestion_version;
      IF state IS DISTINCT FROM 'processing' THEN
        RAISE EXCEPTION 'Source rows require a processing extraction';
      END IF;
      IF TG_TABLE_NAME = 'document_elements' THEN
        SELECT text_raw INTO page_text FROM document_pages
        WHERE workspace_id=NEW.workspace_id AND document_id=NEW.document_id
          AND ingestion_version=NEW.ingestion_version AND id=NEW.page_id;
        IF page_text IS NULL OR NEW.end_char > char_length(page_text)
          OR substring(page_text FROM NEW.start_char+1 FOR NEW.end_char-NEW.start_char)
             IS DISTINCT FROM NEW.text_raw THEN
          RAISE EXCEPTION 'Invalid raw source range';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER source_page_insert BEFORE INSERT ON document_pages
      FOR EACH ROW EXECUTE FUNCTION check_source_insert();
    CREATE TRIGGER source_element_insert BEFORE INSERT ON document_elements
      FOR EACH ROW EXECUTE FUNCTION check_source_insert();
    """)
    op.execute("""
    CREATE FUNCTION protect_version() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.extraction_status IN ('complete','needs_review') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Promoted extraction is immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_version BEFORE UPDATE ON ingestion_versions
      FOR EACH ROW EXECUTE FUNCTION protect_version();
    CREATE FUNCTION check_preview() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.preview_ingestion_version IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM ingestion_versions WHERE workspace_id=NEW.workspace_id
          AND document_id=NEW.id AND ingestion_version=NEW.preview_ingestion_version
          AND extraction_status IN ('complete','needs_review')) THEN
        RAISE EXCEPTION 'Preview must reference promoted source';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER valid_preview BEFORE UPDATE OF preview_ingestion_version ON documents
      FOR EACH ROW EXECUTE FUNCTION check_preview();
    """)


def downgrade():
    op.execute("DROP TRIGGER valid_preview ON documents")
    op.execute("DROP FUNCTION check_preview()")
    op.execute("DROP TRIGGER immutable_version ON ingestion_versions")
    op.execute("DROP FUNCTION protect_version()")
    op.execute("DROP TRIGGER source_page_insert ON document_pages")
    op.execute("DROP TRIGGER source_element_insert ON document_elements")
    op.execute("DROP FUNCTION check_source_insert()")
    op.execute("DROP TRIGGER immutable_page ON document_pages")
    op.execute("DROP TRIGGER immutable_element ON document_elements")
    op.execute("DROP FUNCTION protect_source()")
    op.execute("ALTER TABLE documents DROP CONSTRAINT fk_documents_latest_ingestion_version")
    op.execute("ALTER TABLE documents DROP CONSTRAINT fk_documents_preview_ingestion_version")
    for table in [
        "request_keys",
        "document_elements",
        "document_pages",
        "jobs",
        "ingestion_versions",
        "documents",
        "workspaces",
    ]:
        op.drop_table(table)
