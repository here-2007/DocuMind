"""Build a new extraction from an explicit human review, preserving the original."""

import sqlalchemy as sa

from documind import schema as t
from documind.errors import AppError
from documind.parser import REVIEW_VERSION, _canonical


def reviewed_pages(repo, job, config):
    workspace, document = job["workspace_id"], job["document_id"]
    review = config["review"]
    base = review["base_ingestion_version"]
    pages = repo.source(workspace, document, base)
    with repo.engine.connect() as connection:
        rows = (
            connection.execute(
                sa.select(t.elements)
                .where(
                    t.elements.c.workspace_id == workspace,
                    t.elements.c.document_id == document,
                    t.elements.c.ingestion_version == base,
                )
                .order_by(t.elements.c.reading_order)
            )
            .mappings()
            .all()
        )
    changes = {item["element_id"]: item for item in review["corrections"]}
    output = []
    total_chars = 0
    for page in pages:
        repo.heartbeat(job)
        blocks, tables = [], []
        children = {}
        for row in rows:
            if row["page_id"] != page["id"] or row["element_type"] == "table":
                continue
            change = changes.get(str(row["id"]))
            text = change["text_raw"] if change else row["text_raw"]
            metadata = {
                **row["metadata_json"],
                "reviewer_id": config["reviewer_id"],
                "reviewed_from_element_id": str(row["id"]),
                "base_ingestion_version": base,
                "review_version": REVIEW_VERSION,
            }
            item = {
                "source_order": row["reading_order"],
                "text_raw": text,
                "bbox_norm": row["bbox_norm"],
                "confidence": None if change else row["confidence"],
                "source_type": "manual_correction" if change else row["source_type"],
                "metadata_json": metadata,
            }
            if row["element_type"] == "table_cell":
                coordinates = dict(row["table_coordinates"])
                if change and change["is_header"] is not None:
                    coordinates["is_header"] = change["is_header"]
                children.setdefault(row["parent_element_id"], []).append(
                    {**item, **coordinates, "box": item["bbox_norm"]}
                )
            else:
                blocks.append(item)
        for row in rows:
            if row["page_id"] != page["id"] or row["element_type"] != "table":
                continue
            cells = children.get(row["id"], [])
            headers = [cell["text_raw"] for cell in cells if cell["is_header"]]
            tables.append(
                {
                    "source_order": row["reading_order"],
                    "box": row["bbox_norm"],
                    "cells": cells,
                    "rows": row["metadata_json"]["row_count"],
                    "columns": row["metadata_json"]["column_count"],
                    "headers": headers or None,
                    "extractor": REVIEW_VERSION,
                    "source_type": "manual_correction",
                    "metadata_json": {
                        "reviewer_id": config["reviewer_id"],
                        "base_ingestion_version": base,
                        "reviewed_from_element_id": str(row["id"]),
                    },
                }
            )
        raw, elements = _canonical(blocks, tables, preserve_order=True)
        if not raw.strip():
            raise AppError("EMPTY_REVIEW_SOURCE", 422)
        total_chars += len(raw)
        if total_chars > config["max_extracted_chars"]:
            raise AppError("EXTRACTED_TEXT_LIMIT", 422)
        result = {key: page[key] for key in ("page_number", "width", "height", "rotation")}
        result.update(
            {
                "text_raw": raw,
                "elements": elements,
                "processing_status": "complete",
                "metadata_json": {
                    **page["metadata_json"],
                    "warnings": [],
                    "reviewer_id": config["reviewer_id"],
                    "base_ingestion_version": base,
                    "original_warnings": page["metadata_json"].get("warnings", []),
                },
                "image": repo.objects.read_preview(page),
            }
        )
        output.append(result)
    return output
