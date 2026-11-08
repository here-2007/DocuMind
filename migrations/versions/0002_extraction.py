"""OCR provenance, ruled-table cells and immutable review support."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002_extraction"
down_revision = "0001_native"
branch_labels = None
depends_on = None

SPAN_V1 = "reading_order >= 0 AND start_char >= 0 AND end_char > start_char AND end_char - start_char = char_length(text_raw)"
SPAN_V2 = "reading_order >= 0 AND start_char >= 0 AND end_char >= start_char AND (end_char > start_char OR element_type = 'table_cell') AND end_char - start_char = char_length(text_raw)"
IDENTITY = "element_type IN ('paragraph','table','table_cell') AND source_type IN ('native_text','ocr','manual_correction') AND (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))"
STRUCTURE = "(element_type = 'table_cell' AND parent_element_id IS NOT NULL AND table_coordinates IS NOT NULL AND table_coordinates ?& ARRAY['row_index','column_index','row_span','column_span','is_header'] AND (table_coordinates->>'row_index')::int >= 0 AND (table_coordinates->>'column_index')::int >= 0 AND (table_coordinates->>'row_span')::int >= 1 AND (table_coordinates->>'column_span')::int >= 1) OR (element_type <> 'table_cell' AND parent_element_id IS NULL AND table_coordinates IS NULL)"


def upgrade():
    for table in ("document_pages", "document_elements"):
        op.add_column(
            table,
            sa.Column(
                "metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
            ),
        )
    op.add_column("document_elements", sa.Column("parent_element_id", UUID))
    op.add_column("document_elements", sa.Column("table_coordinates", JSONB))
    scope = ["workspace_id", "document_id", "ingestion_version", "page_id"]
    op.create_unique_constraint(
        "uq_element_scoped_parent",
        "document_elements",
        [*scope, "id"],
    )
    op.create_foreign_key(
        "fk_element_table_parent",
        "document_elements",
        "document_elements",
        [*scope, "parent_element_id"],
        [*scope, "id"],
    )
    op.drop_constraint(op.f("ck_document_elements_span"), "document_elements", type_="check")
    op.create_check_constraint(op.f("ck_document_elements_span"), "document_elements", SPAN_V2)
    op.create_check_constraint(
        op.f("ck_document_elements_extraction_identity"), "document_elements", IDENTITY
    )
    op.create_check_constraint(
        op.f("ck_document_elements_table_structure"), "document_elements", STRUCTURE
    )
    op.execute("""
    CREATE FUNCTION check_table_parent() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.element_type = 'table_cell' AND NOT EXISTS (
        SELECT 1 FROM document_elements WHERE id=NEW.parent_element_id
          AND workspace_id=NEW.workspace_id AND document_id=NEW.document_id
          AND ingestion_version=NEW.ingestion_version AND page_id=NEW.page_id
          AND element_type='table'
          AND NEW.start_char >= start_char AND NEW.end_char <= end_char
      ) THEN RAISE EXCEPTION 'Invalid table parent/span'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER valid_table_parent BEFORE INSERT ON document_elements
      FOR EACH ROW EXECUTE FUNCTION check_table_parent();
    """)


def downgrade():
    # Reverting with table/OCR sources would discard provenance or violate v1 spans.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM document_elements WHERE element_type <> 'paragraph' OR source_type <> 'native_text')
      THEN RAISE EXCEPTION 'Cannot downgrade populated Phase 1B provenance'; END IF;
    END $$;""")
    op.execute("DROP TRIGGER valid_table_parent ON document_elements")
    op.execute("DROP FUNCTION check_table_parent()")
    for name in ("table_structure", "extraction_identity", "span"):
        op.drop_constraint(op.f("ck_document_elements_" + name), "document_elements", type_="check")
    op.create_check_constraint(op.f("ck_document_elements_span"), "document_elements", SPAN_V1)
    op.drop_constraint("fk_element_table_parent", "document_elements", type_="foreignkey")
    op.drop_constraint(
        "uq_element_scoped_parent",
        "document_elements",
        type_="unique",
    )
    for column in ("table_coordinates", "parent_element_id", "metadata_json"):
        op.drop_column("document_elements", column)
    op.drop_column("document_pages", "metadata_json")
