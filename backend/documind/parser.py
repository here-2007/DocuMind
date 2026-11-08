"""Bounded PDF extraction with optional local OCR and ruled-table structure."""

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

from documind.errors import AppError

PARSER_VERSION = f"pymupdf-{pymupdf.VersionBind}-pdf-v2"
REVIEW_VERSION = "manual-review-v1"


def fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def _extract(source: Path, target: Path, config: dict):
    if not source.read_bytes().startswith(b"%PDF-"):
        raise AppError("INVALID_DOCUMENT")
    try:
        doc = pymupdf.open(source)
    except (RuntimeError, ValueError) as exc:
        raise AppError("UNPROCESSABLE_DOCUMENT", 422) from exc
    with doc:
        if doc.needs_pass:
            raise AppError("ENCRYPTED_PDF", 422)
        if not 0 < len(doc) <= config["max_pdf_pages"]:
            raise AppError("PDF_PAGE_LIMIT", 422)
        pages = []
        chars = total_pixels = total_bytes = 0
        ocr_engine = None
        for number, page in enumerate(doc, 1):
            rotation = page.rotation
            rotation_matrix = page.rotation_matrix
            rendered_width, rendered_height = page.rect.width, page.rect.height
            page.set_rotation(0)
            width, height = page.rect.width, page.rect.height
            if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
                raise AppError("INVALID_PAGE_GEOMETRY", 422)
            native = [
                block
                for block in page.get_text("blocks", sort=True)
                if block[6] == 0 and block[4].strip()
            ]
            text_chars = sum(len("".join(block[4].split())) for block in native)
            image_area = sum(
                (pymupdf.Rect(image["bbox"]) & page.rect).get_area()
                for image in page.get_image_info()
            )
            needs_ocr = (
                text_chars < config.get("ocr_min_native_chars", 8)
                or image_area > width * height * 0.5
            )
            use_ocr = config.get("ocr_enabled", False) and needs_ocr
            scale = config.get("ocr_dpi", 144) / 72 if use_ocr else 1
            pixels = math.ceil(width * scale) * math.ceil(height * scale)
            if pixels > config["max_page_pixels"]:
                raise AppError("PAGE_PIXEL_LIMIT", 422)
            # Count OCR raster and preview before allocating either image.
            total_pixels += pixels + (math.ceil(width) * math.ceil(height) if use_ocr else 0)
            if total_pixels > config["max_document_pixels"]:
                raise AppError("DOCUMENT_PIXEL_LIMIT", 422)
            tables = []
            if use_ocr:
                from documind.ocr import load_engine, recognize
                from documind.tables import raster_tables

                image_path = target / "ocr.png"
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                pix.save(image_path)
                if image_path.stat().st_size + total_bytes > config["max_rendered_bytes"]:
                    raise AppError("RENDERED_BYTES_LIMIT", 422)
                if ocr_engine is None:
                    ocr_engine = load_engine(Path(config["ocr_model_root"]), config["ocr_identity"])
                blocks = recognize(ocr_engine, image_path, pix.width, pix.height)
                if config.get("tables_enabled", True):
                    tables = raster_tables(image_path, blocks, config.get("max_table_cells", 1000))
                extractor = "paddleocr"
            else:
                blocks = [
                    {
                        "text_raw": b[4],
                        "bbox_norm": [b[0] / width, b[1] / height, b[2] / width, b[3] / height],
                        "source_type": "native_text",
                        "confidence": None,
                    }
                    for b in native
                ]
                if config.get("tables_enabled", True):
                    from documind.tables import native_tables

                    tables = native_tables(page, config.get("max_table_cells", 1000))
                    for table in tables:
                        table["box"] = _norm(table["box"], width, height)
                        for cell in table["cells"]:
                            cell["box"] = _norm(cell["box"], width, height)
                    if tables:
                        blocks = _outside_table_words(page, tables, width, height)
                extractor = "pymupdf"
            raw, elements = _canonical(blocks, tables)
            chars += len(raw)
            if chars > config["max_extracted_chars"]:
                raise AppError("EXTRACTED_TEXT_LIMIT", 422)
            for element in elements:
                rect = pymupdf.Rect(
                    [
                        element["bbox_norm"][0] * width,
                        element["bbox_norm"][1] * height,
                        element["bbox_norm"][2] * width,
                        element["bbox_norm"][3] * height,
                    ]
                )
                rect *= rotation_matrix
                element["bbox_norm"] = _norm(rect, rendered_width, rendered_height)
                element["metadata_json"].update({"extractor_version": PARSER_VERSION})
            page.set_rotation(rotation)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
            pix.save(target / f"{number}.png")
            total_bytes += (target / f"{number}.png").stat().st_size
            if total_bytes > config["max_rendered_bytes"]:
                raise AppError("RENDERED_BYTES_LIMIT", 422)
            warnings = []
            if needs_ocr and not use_ocr:
                warnings.append("OCR_REQUIRED")
            if use_ocr:
                warnings.append("OCR_REVIEW_REQUIRED")
            if tables:
                warnings.append("TABLE_REVIEW_REQUIRED")
            if not elements:
                warnings.append("NO_TEXT")
            pages.append(
                {
                    "page_number": number,
                    "width": rendered_width,
                    "height": rendered_height,
                    "rotation": rotation,
                    "text_raw": raw,
                    "elements": elements,
                    "processing_status": "needs_review" if warnings else "complete",
                    "metadata_json": {
                        "extractor_name": extractor,
                        "warnings": warnings,
                        "ocr_dpi": config.get("ocr_dpi", 144) if use_ocr else None,
                        "ocr_identity": config.get("ocr_identity") if use_ocr else None,
                    },
                }
            )
        (target / "result.json").write_text(json.dumps(pages), encoding="utf-8")


def _norm(box, width, height):
    return [
        max(0, min(1, box[0] / width)),
        max(0, min(1, box[1] / height)),
        max(0, min(1, box[2] / width)),
        max(0, min(1, box[3] / height)),
    ]


def _canonical(blocks, tables):
    from documind.tables import contains

    items = [
        (block["bbox_norm"], block)
        for block in blocks
        if not any(contains(table["box"], block["bbox_norm"]) for table in tables)
    ]
    items.extend((table["box"], table) for table in tables)
    raw, elements = "", []
    for box, item in sorted(items, key=lambda entry: (entry[0][1], entry[0][0])):
        start = len(raw)
        if "cells" not in item:
            text = item["text_raw"]
            raw += text
            elements.append(
                {
                    **item,
                    "element_type": "paragraph",
                    "start_char": start,
                    "end_char": len(raw),
                    "metadata_json": {
                        "extractor_name": "paddleocr"
                        if item["source_type"] == "ocr"
                        else "pymupdf",
                        **item.get("metadata_json", {}),
                    },
                }
            )
        else:
            parent = len(elements)
            children = []
            for cell in item["cells"]:
                offset = len(raw)
                raw += cell["text_raw"]
                children.append(
                    {
                        "text_raw": cell["text_raw"],
                        "bbox_norm": cell["box"],
                        "element_type": "table_cell",
                        "source_type": cell.get("source_type", item["source_type"]),
                        "confidence": cell["confidence"],
                        "start_char": offset,
                        "end_char": len(raw),
                        "parent_order": parent,
                        "table_coordinates": {
                            "row_index": cell["row_index"],
                            "column_index": cell["column_index"],
                            "row_span": cell.get("row_span", 1),
                            "column_span": cell.get("column_span", 1),
                            "is_header": cell["is_header"],
                        },
                        "metadata_json": {
                            "extractor_name": item["extractor"],
                            "header_status": "inferred"
                            if cell["is_header"] is not None
                            else "unknown",
                            **cell.get("metadata_json", {}),
                        },
                    }
                )
                raw += (
                    "\t"
                    if cell["column_index"] + cell.get("column_span", 1) < item["columns"]
                    else "\n"
                )
            elements.append(
                {
                    "text_raw": raw[start:],
                    "bbox_norm": box,
                    "element_type": "table",
                    "source_type": item["source_type"],
                    "confidence": None,
                    "start_char": start,
                    "end_char": len(raw),
                    "metadata_json": {
                        "extractor_name": item["extractor"],
                        "row_count": item["rows"],
                        "column_count": item["columns"],
                        "headers": item["headers"],
                        "continuation": "unknown",
                        **item.get("metadata_json", {}),
                    },
                }
            )
            elements.extend(children)
        if not raw.endswith("\n"):
            raw += "\n"
    return raw, elements


def _outside_table_words(page, tables, width, height):
    from documind.tables import contains

    lines = {}
    for word in page.get_text("words", sort=True):
        box = _norm(word[:4], width, height)
        if any(contains(table["box"], box) for table in tables):
            continue
        lines.setdefault((word[5], word[6]), []).append(word)
    return [
        {
            "text_raw": " ".join(word[4] for word in words) + "\n",
            "bbox_norm": _norm(
                [
                    min(w[0] for w in words),
                    min(w[1] for w in words),
                    max(w[2] for w in words),
                    max(w[3] for w in words),
                ],
                width,
                height,
            ),
            "source_type": "native_text",
            "confidence": None,
        }
        for words in lines.values()
    ]


def _child(source: str, target: str, config: dict):
    # Parser runs in an expendable process, so C-library crashes cannot kill the API/worker.
    try:
        _extract(Path(source), Path(target), config)
    except AppError as exc:
        Path(target, "error.txt").write_text(exc.code)
    except Exception:
        Path(target, "error.txt").write_text("UNPROCESSABLE_DOCUMENT")


def extract_pdf(data: bytes, config: dict, timeout: int, heartbeat=lambda: None) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="documind-parse-") as folder:
        target = Path(folder)
        source = target / "original.pdf"
        source.write_bytes(data)
        process = subprocess.Popen(
            [sys.executable, "-m", "documind.parser", str(source), str(target), json.dumps(config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time

        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                try:
                    process.wait(timeout=min(1, max(0.01, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
                heartbeat()
                if process.poll() is None and time.monotonic() >= deadline:
                    raise AppError("PARSER_TIMEOUT", 422)
            if (target / "error.txt").exists():
                raise AppError((target / "error.txt").read_text(), 422)
            if process.returncode != 0 or not (target / "result.json").is_file():
                raise AppError("PARSER_CRASH", 422)
            pages = json.loads((target / "result.json").read_text())
            for page in pages:
                page["image"] = (target / f"{page['page_number']}.png").read_bytes()
            return pages
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


if __name__ == "__main__":
    _child(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]))
