import hashlib
from pathlib import Path

import pymupdf
import pytest
from documind.errors import AppError
from documind.parser import extract_pdf
from documind.storage import ObjectStore

CONFIG = {
    "max_pdf_pages": 10,
    "max_page_pixels": 1000000,
    "max_document_pixels": 2000000,
    "max_rendered_bytes": 1000000,
    "max_extracted_chars": 10000,
}


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_geometry_and_raw_offsets(pdf, rotation):
    parsed = extract_pdf(pdf(rotation=rotation), CONFIG, 10)
    p = parsed[0]
    e = p["elements"][0]
    assert p["page_number"] == 1
    assert p["text_raw"][e["start_char"] : e["end_char"]] == e["text_raw"]
    assert "Grand total" in e["text_raw"]
    assert p["image"].startswith(b"\x89PNG")
    pix = pymupdf.Pixmap(p["image"])
    x0, y0, x1, y1 = e["bbox_norm"]
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    # The reported box overlaps the rendered text in every rotation.
    clip = pymupdf.IRect(x0 * pix.width, y0 * pix.height, x1 * pix.width, y1 * pix.height)
    assert clip.get_area() > 0
    assert pix.color_count(clip=clip) > 1


def test_empty_scan_review_and_limits(pdf):
    assert extract_pdf(pdf(blank=True), CONFIG, 10)[0]["processing_status"] == "needs_review"
    with pytest.raises(AppError, match="PAGE_PIXEL_LIMIT"):
        extract_pdf(pdf(), {**CONFIG, "max_page_pixels": 1}, 10)
    with pytest.raises(AppError, match="EXTRACTED_TEXT_LIMIT"):
        extract_pdf(pdf(), {**CONFIG, "max_extracted_chars": 1}, 10)
    with pytest.raises(AppError, match="UNPROCESSABLE_DOCUMENT"):
        extract_pdf(b"%PDF-invalid", CONFIG, 10)
    with pytest.raises(AppError, match="INVALID_DOCUMENT"):
        extract_pdf(b"other", CONFIG, 10)


def test_encrypted_pdf(pdf):
    with pymupdf.open(stream=pdf(), filetype="pdf") as doc:
        data = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader"
        )
    with pytest.raises(AppError, match="ENCRYPTED_PDF"):
        extract_pdf(data, CONFIG, 10)


def test_private_storage_atomic_immutable_and_bounded(tmp_path):
    objects = ObjectStore(tmp_path / "objects")
    objects.initialize()
    objects.put("a/b.pdf", b"hello", "application/pdf")
    objects.put("a/b.pdf", b"hello", "application/pdf")
    assert hashlib.sha256(objects.get("a/b.pdf", 5)).digest() == hashlib.sha256(b"hello").digest()
    with pytest.raises(ValueError):
        objects.get("a/b.pdf", 4)
    with pytest.raises(ValueError):
        objects.put("a/b.pdf", b"different", "application/pdf")
    for key in ["../escape", "/absolute"]:
        with pytest.raises(ValueError):
            objects.put(key, b"x", "text/plain")
    (objects.root / "link").symlink_to(Path(tmp_path).parent)
    with pytest.raises(ValueError):
        objects.put("link/escape", b"x", "text/plain")
    assert (objects.path("a/b.pdf").stat().st_mode & 0o077) == 0


def test_configuration_rejects_unimplemented_capabilities(monkeypatch):
    from documind.config import Settings

    monkeypatch.setenv("GENERATION_ENABLED", "true")
    with pytest.raises(ValueError, match="not implemented"):
        Settings(
            _env_file=None,
            local_api_token="x" * 32,
            database_url="postgresql://unused",
            object_store_access_key="local",
            object_store_secret_key="unused",
        )


def test_actual_image_only_pdf_requires_review(pdf):
    with pymupdf.open(stream=pdf(), filetype="pdf") as original:
        png = original[0].get_pixmap().tobytes("png")
    with pymupdf.open() as scanned:
        p = scanned.new_page(width=300, height=400)
        p.insert_image(p.rect, stream=png)
        data = scanned.tobytes()
    result = extract_pdf(data, CONFIG, 10)[0]
    assert result["processing_status"] == "needs_review"
    assert result["elements"] == [] and result["text_raw"] == ""
    assert result["image"].startswith(b"\x89PNG")


def test_document_limits_and_parser_deadline(pdf):
    with pymupdf.open(stream=pdf(), filetype="pdf") as doc:
        doc.new_page()
        data = doc.tobytes()
    with pytest.raises(AppError, match="PDF_PAGE_LIMIT"):
        extract_pdf(data, {**CONFIG, "max_pdf_pages": 1}, 10)
    with pytest.raises(AppError, match="DOCUMENT_PIXEL_LIMIT"):
        extract_pdf(pdf(), {**CONFIG, "max_document_pixels": 1}, 10)
    with pytest.raises(AppError, match="RENDERED_BYTES_LIMIT"):
        extract_pdf(pdf(), {**CONFIG, "max_rendered_bytes": 1}, 10)
    with pytest.raises(AppError, match="PARSER_TIMEOUT"):
        extract_pdf(pdf(), CONFIG, 0.001)
