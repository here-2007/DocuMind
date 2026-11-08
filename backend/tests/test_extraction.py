"""Synthetic fixtures are generated here; no confidential corpus or precomputed OCR."""

import os
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
import pytest
import sqlalchemy as sa
from documind import schema as t
from documind.errors import AppError
from documind.ocr import model_identity
from documind.parser import extract_pdf
from sqlalchemy.exc import DBAPIError

CONFIG = {
    "max_pdf_pages": 10,
    "max_page_pixels": 2000000,
    "max_document_pixels": 8000000,
    "max_rendered_bytes": 8000000,
    "max_extracted_chars": 10000,
    "tables_enabled": True,
}


@pytest.fixture
def table_pdf():
    def make(scanned=False, rotation=0):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=300)
            for x in (30, 150, 270):
                page.draw_line((x, 60), (x, 180))
            for y in (60, 100, 140, 180):
                page.draw_line((30, y), (270, y))
            for x, y, text in (
                (40, 85, "Item"),
                (160, 85, "Amount"),
                (40, 125, "Pens"),
                (160, 125, "12.50"),
                (40, 165, "Paper"),
            ):
                page.insert_text((x, y), text, fontsize=14)
            if scanned:
                png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
                with pymupdf.open() as scan:
                    p = scan.new_page(width=300, height=300)
                    p.insert_image(p.rect, stream=png)
                    p.set_rotation(rotation)
                    return scan.tobytes()
            page.set_rotation(rotation)
            return doc.tobytes()

    return make


def assert_table(page, source):
    tables = [e for e in page["elements"] if e["element_type"] == "table"]
    cells = [e for e in page["elements"] if e["element_type"] == "table_cell"]
    assert len(tables) == 1 and len(cells) == 6
    assert page["processing_status"] == "needs_review"
    actual = {
        (e["table_coordinates"]["row_index"], e["table_coordinates"]["column_index"]): e
        for e in cells
    }
    expected = {
        (0, 0): "Item",
        (0, 1): "Amount",
        (1, 0): "Pens",
        (1, 1): "12.50",
        (2, 0): "Paper",
        (2, 1): "",
    }
    assert {k: v["text_raw"] for k, v in actual.items()} == expected
    for element in page["elements"]:
        assert element["source_type"] == source
        assert page["text_raw"][element["start_char"] : element["end_char"]] == element["text_raw"]
        x0, y0, x1, y1 = element["bbox_norm"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert all(e["parent_order"] == page["elements"].index(tables[0]) for e in cells)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_native_table_geometry_offsets_and_empty_cells(table_pdf, rotation):
    page = extract_pdf(table_pdf(rotation=rotation), CONFIG, 15)[0]
    assert_table(page, "native_text")
    headers = [e for e in page["elements"] if e.get("table_coordinates", {}).get("is_header")]
    assert [e["text_raw"] for e in headers] == ["Item", "Amount"]
    with pytest.raises(AppError, match="TABLE_CELL_LIMIT"):
        extract_pdf(table_pdf(), {**CONFIG, "max_table_cells": 2}, 15)


def test_review_creates_immutable_version_and_preserves_old_sources(client, repo, table_pdf):
    from documind.worker import process_job

    workspace = repo.cfg.local_workspace_id
    row = repo.upload(workspace, table_pdf(), "table.pdf", "table")
    process_job(row["job_id"])
    base = row["latest_ingestion_version"]
    path = f"/api/v1/workspaces/{workspace}/documents/{row['document_id']}"
    before = client.get(path + "/pages/1").json()
    original = client.get(path + "/elements", params={"element_type": "table_cell"}).json()["items"]
    assert len(original) == 6 and all(e["element_type"] == "table_cell" for e in original)
    changed = next(e for e in original if e["text_raw"] == "12.50")
    body = {
        "base_ingestion_version": base,
        "confirm_all_pages": True,
        "corrections": [{"element_id": changed["id"], "text_raw": "13.50"}],
    }
    response = client.post(path + "/review", json=body, headers={"Idempotency-Key": "review"})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["latest_ingestion_version"] != base
    assert result["preview_ingestion_version"] == base
    process_job(UUID(result["job_id"]))
    assert repo.job(workspace, UUID(result["job_id"]))["status"] == "succeeded"
    summary = client.get(path).json()
    assert summary["extraction_status"] == "complete" and not summary["searchable"]
    assert summary["preview_ingestion_version"] == result["latest_ingestion_version"]
    updated = client.get(path + "/pages/1").json()
    assert "13.50" in updated["text_raw"] and "12.50" not in updated["text_raw"]
    old = client.get(path + "/pages/1", params={"ingestion_version": base}).json()
    assert old["text_raw"] == before["text_raw"]
    new_elements = client.get(path + "/elements").json()["items"]
    new_cell = next(e for e in new_elements if e["text_raw"] == "13.50")
    assert new_cell["id"] not in {e["id"] for e in original}
    assert new_cell["source_type"] == "manual_correction"
    assert new_cell["metadata_json"]["reviewed_from_element_id"] == changed["id"]
    assert new_cell["parent_element_id"] in {e["id"] for e in new_elements}
    assert (
        client.post(path + "/review", json=body, headers={"Idempotency-Key": "review"}).json()[
            "job_id"
        ]
        == result["job_id"]
    )
    body["corrections"][0]["text_raw"] = "14.50"
    assert (
        client.post(path + "/review", json=body, headers={"Idempotency-Key": "review"}).status_code
        == 409
    )
    body["corrections"][0]["element_id"] = str(uuid4())
    assert (
        client.post(
            path + "/review", json=body, headers={"Idempotency-Key": "wrong-id"}
        ).status_code
        == 422
    )
    with repo.engine.begin() as c, pytest.raises(DBAPIError, match="immutable"):
        c.execute(
            t.elements.update()
            .where(t.elements.c.id == UUID(new_cell["id"]))
            .values(text_raw="edited")
        )
    deletion = client.delete(path).json()["job_id"]
    process_job(UUID(deletion))
    assert client.get(path + "/pages/1").status_code == 410
    with repo.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(t.elements)).scalar_one() == 0


@pytest.fixture
def ocr_config():
    root = os.getenv("DOCUMIND_TEST_OCR_ROOT")
    if not root:
        pytest.skip("Set DOCUMIND_TEST_OCR_ROOT to run real local model tests")
    path = Path(root).resolve()
    return {
        **CONFIG,
        "ocr_enabled": True,
        "ocr_model_root": str(path),
        "ocr_identity": model_identity(path),
        "ocr_dpi": 144,
    }


@pytest.mark.ocr
def test_real_scanned_table_and_confidence(table_pdf, ocr_config):
    page = extract_pdf(table_pdf(scanned=True), ocr_config, 90)[0]
    assert_table(page, "ocr")
    cells = [e for e in page["elements"] if e["element_type"] == "table_cell" and e["text_raw"]]
    assert all(0 <= e["confidence"] <= 1 for e in cells)
    assert "OCR_REVIEW_REQUIRED" in page["metadata_json"]["warnings"]
    assert page["metadata_json"]["ocr_identity"] == ocr_config["ocr_identity"]


@pytest.mark.ocr
def test_real_mixed_pdf_and_rotated_scan(pdf, ocr_config):
    with pymupdf.open(stream=pdf("INVOICE 12345"), filetype="pdf") as original:
        png = original[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    with pymupdf.open(stream=pdf(), filetype="pdf") as mixed:
        page = mixed.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=png)
        page.set_rotation(90)
        data = mixed.tobytes()
    pages = extract_pdf(data, ocr_config, 90)
    assert pages[0]["metadata_json"]["extractor_name"] == "pymupdf"
    assert pages[0]["processing_status"] == "complete"
    assert "INVOICE 12345" in pages[1]["text_raw"]
    assert pages[1]["rotation"] == 90
    assert all(e["source_type"] == "ocr" for e in pages[1]["elements"])
    assert pages[1]["processing_status"] == "needs_review"


def test_adjacent_text_and_merged_table_headers_survive():
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        for x in (60, 280):
            page.draw_line((x, 60), (x, 180))
        page.draw_line((170, 100), (170, 180))
        for y in (60, 100, 140, 180):
            page.draw_line((60, y), (280, y))
        for x, y, text in (
            (10, 85, "ID:"),
            (70, 85, "Header"),
            (70, 125, "Pens"),
            (180, 125, "12.50"),
            (70, 165, "Paper"),
            (180, 165, "10.00"),
        ):
            page.insert_text((x, y), text)
        data = doc.tobytes()
    result = extract_pdf(data, CONFIG, 15)[0]
    assert "ID:" in result["text_raw"]
    header = next(
        e
        for e in result["elements"]
        if e["element_type"] == "table_cell" and e["text_raw"] == "Header"
    )
    assert header["table_coordinates"]["column_span"] == 2
    parent = result["elements"][header["parent_order"]]
    assert parent["text_raw"].startswith("Header\nPens\t12.50\n")


def test_reprocessing_keeps_original_idempotency_fingerprint(repo, pdf):
    from documind.parser import fingerprint
    from documind.worker import process_job

    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "a")
    process_job(row["job_id"])
    repo.reprocess(repo.cfg.local_workspace_id, row["document_id"], "reprocess")
    with repo.engine.connect() as c:
        digest = c.execute(
            sa.select(t.request_keys.c.request_hash).where(
                t.request_keys.c.operation == "reprocess", t.request_keys.c.key == "reprocess"
            )
        ).scalar_one()
    assert digest == fingerprint({"document_id": str(row["document_id"])})


@pytest.mark.ocr
def test_real_ocr_job_api_and_review(client, repo, table_pdf, ocr_config, monkeypatch):
    from documind.worker import process_job

    monkeypatch.setattr(repo.cfg, "ocr_enabled", True)
    monkeypatch.setattr(repo.cfg, "ocr_model_root", Path(ocr_config["ocr_model_root"]))
    workspace = repo.cfg.local_workspace_id
    path = f"/api/v1/workspaces/{workspace}/documents"
    response = client.post(
        path,
        files={"file": ("scan.pdf", table_pdf(scanned=True))},
        headers={"Idempotency-Key": "scan"},
    )
    assert response.status_code == 202
    row = response.json()
    process_job(UUID(row["job_id"]))
    path += "/" + row["document_id"]
    summary = client.get(path).json()
    assert summary["extraction_status"] == "needs_review" and summary["preview_available"]
    assert summary["searchable"] is False
    page = client.get(path + "/pages/1").json()
    assert "12.50" in page["text_raw"]
    cells = client.get(path + "/elements", params={"element_type": "table_cell"}).json()["items"]
    assert len(cells) == 6 and all(e["source_type"] == "ocr" for e in cells)
    response = client.post(
        path + "/review",
        json={
            "base_ingestion_version": summary["preview_ingestion_version"],
            "confirm_all_pages": True,
        },
        headers={"Idempotency-Key": "accept-scan"},
    )
    assert response.status_code == 202
    process_job(UUID(response.json()["job_id"]))
    assert client.get(path).json()["extraction_status"] == "complete"
    old = client.get(
        path + "/pages/1", params={"ingestion_version": summary["preview_ingestion_version"]}
    ).json()
    assert old["processing_status"] == "needs_review"
    deletion = client.delete(path).json()["job_id"]
    process_job(UUID(deletion))
    assert client.get(path + "/elements").status_code == 410


@pytest.mark.parametrize("bad", ["null-coordinate", "wrong-parent", "array-coordinates"])
def test_invalid_table_provenance_rejected_by_postgres(repo, table_pdf, bad):
    row = repo.upload(repo.cfg.local_workspace_id, table_pdf(), "table.pdf", "invalid")
    job = repo.claim(row["job_id"])
    pages = extract_pdf(table_pdf(), repo.parser_config(), 15)
    cell = next(e for e in pages[0]["elements"] if e["element_type"] == "table_cell")
    if bad == "null-coordinate":
        cell["table_coordinates"]["row_index"] = None
    elif bad == "array-coordinates":
        cell["table_coordinates"] = [
            "row_index",
            "column_index",
            "row_span",
            "column_span",
            "is_header",
        ]
    else:
        cell["parent_order"] = 9999
    with pytest.raises(DBAPIError):
        repo.promote(job, pages)
    with repo.engine.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(t.pages)).scalar_one() == 0


def test_missing_ocr_artifacts_fail_explicitly(client, repo, monkeypatch, tmp_path, pdf):
    monkeypatch.setattr(repo.cfg, "ocr_enabled", True)
    monkeypatch.setattr(repo.cfg, "ocr_model_root", tmp_path / "missing")
    response = client.get("/api/v1/ready")
    assert response.status_code == 503 and response.json()["ocr"] is False
    path = f"/api/v1/workspaces/{repo.cfg.local_workspace_id}/documents"
    response = client.post(
        path, files={"file": ("a.pdf", pdf())}, headers={"Idempotency-Key": "missing-model"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OCR_MODEL_UNAVAILABLE"
    with repo.engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(t.documents)).scalar_one()
            == 0
        )
