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

    monkeypatch.setenv("SELF_RAG_ENABLED", "true")
    with pytest.raises(ValueError, match="Self-RAG"):
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


def test_pdf_child_has_private_environment_and_resource_boundary(pdf, monkeypatch):
    import sys

    from documind import parser

    launch = parser.subprocess.Popen
    marker = "DOCUMIND_TEST_OPERATOR_SECRET"
    monkeypatch.setenv(marker, "synthetic")
    observed = []

    def capture(args, **settings):
        if args[:4] == [sys.executable, "-I", "-B", "-m"] and args[4] == "documind.parser":
            observed.append(settings)
        return launch(args, **settings)

    monkeypatch.setattr(parser.subprocess, "Popen", capture)
    assert extract_pdf(pdf(), CONFIG, 10)[0]["text_raw"]
    assert len(observed) == 1
    settings = observed[0]
    assert marker not in settings["env"]
    assert settings["env"]["HOME"] == settings["env"]["TMPDIR"] == str(settings["cwd"])
    assert settings["start_new_session"] is True


def test_pdf_sandbox_limits_file_and_network_access(tmp_path):
    import json
    import os
    import socket
    import subprocess
    import sys

    work = tmp_path / "input"
    work.mkdir()
    (work / "allowed").write_text("parser input")
    outside = tmp_path / "unrelated"
    outside.write_text("unrelated data")
    source = """
import json, resource, socket, sys
from pathlib import Path
from documind.parser_sandbox import restrict

work, outside, port = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
restrict(work, {"max_rendered_bytes": 1_000_000})
assert (work / "allowed").read_text() == "parser input"
(work / "output").write_text("success")
denied = []
for operation in (outside.read_bytes, lambda: outside.write_text("refused"),
                  lambda: socket.create_connection(("127.0.0.1", port), timeout=1)):
    try:
        result = operation()
        if isinstance(result, socket.socket):
            result.close()
        denied.append(False)
    except PermissionError:
        denied.append(True)
print(json.dumps({
    "denied": denied,
    "core_limit": resource.getrlimit(resource.RLIMIT_CORE)[0],
    "memory_limit": resource.getrlimit(resource.RLIMIT_AS)[0],
    "file_limit": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
}))
"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                source,
                str(work.resolve()),
                str(outside.resolve()),
                str(listener.getsockname()[1]),
            ],
            cwd=work,
            env={"PATH": "/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work)},
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    result = json.loads(completed.stdout)
    assert result["denied"] == [True, True, True]
    assert result["core_limit"] == 0 and result["file_limit"] == 64 * 1024**2
    if os.uname().sysname == "Linux":
        assert 0 < result["memory_limit"] <= 1536 * 1024**2
    assert (work / "output").read_text() == "success"
    assert outside.read_text() == "unrelated data"
