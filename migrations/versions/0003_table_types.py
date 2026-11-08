"""Reject null/string table coordinates that SQL CHECK's null logic can accept."""

from alembic import op

revision = "0003_table_types"
down_revision = "0002_extraction"
branch_labels = None
depends_on = None

STRUCTURE = "(element_type = 'table_cell' AND parent_element_id IS NOT NULL AND table_coordinates IS NOT NULL AND table_coordinates ?& ARRAY['row_index','column_index','row_span','column_span','is_header'] AND (table_coordinates->>'row_index')::int >= 0 AND (table_coordinates->>'column_index')::int >= 0 AND (table_coordinates->>'row_span')::int >= 1 AND (table_coordinates->>'column_span')::int >= 1) OR (element_type <> 'table_cell' AND parent_element_id IS NULL AND table_coordinates IS NULL)"
TYPES = "AND jsonb_typeof(table_coordinates) = 'object' AND jsonb_typeof(table_coordinates->'row_index') = 'number' AND jsonb_typeof(table_coordinates->'column_index') = 'number' AND jsonb_typeof(table_coordinates->'row_span') = 'number' AND jsonb_typeof(table_coordinates->'column_span') = 'number' AND jsonb_typeof(table_coordinates->'is_header') IN ('boolean','null') "


def replace_constraint(expression):
    op.drop_constraint(
        op.f("ck_document_elements_table_structure"), "document_elements", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_elements_table_structure"), "document_elements", expression
    )


def upgrade():
    replace_constraint(
        STRUCTURE.replace(
            "AND (table_coordinates->>'row_index')",
            TYPES + "AND (table_coordinates->>'row_index')",
            1,
        )
    )


def downgrade():
    replace_constraint(STRUCTURE)
