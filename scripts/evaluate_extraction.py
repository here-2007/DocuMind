"""Measure the real local OCR profile on two owned, clean synthetic scans."""

import argparse
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
from documind.ocr import model_identity
from documind.parser import PARSER_VERSION, extract_pdf


def distance(left, right):
    previous = list(range(len(right) + 1))
    for index, a in enumerate(left, 1):
        current = [index]
        for column, b in enumerate(right, 1):
            current.append(
                min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (a != b))
            )
        previous = current
    return previous[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, default=Path(".data/models/ocr"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.models.resolve()
    identity = model_identity(root)
    config = {
        "max_pdf_pages": 5,
        "max_page_pixels": 2000000,
        "max_document_pixels": 4000000,
        "max_rendered_bytes": 4000000,
        "max_extracted_chars": 10000,
        "ocr_enabled": True,
        "ocr_model_root": str(root),
        "ocr_identity": identity,
        "ocr_dpi": 144,
        "tables_enabled": True,
    }
    results = []
    for expected in ("INVOICE 12345", "Total INR 1100.00"):
        with pymupdf.open() as source:
            page = source.new_page(width=300, height=400)
            page.insert_text((30, 80), expected, fontsize=18)
            image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
        with pymupdf.open() as scan:
            page = scan.new_page(width=300, height=400)
            page.insert_image(page.rect, stream=image)
            data = scan.tobytes()
        started = time.monotonic()
        page = extract_pdf(data, config, 90)[0]
        actual = " ".join(page["text_raw"].split())
        results.append(
            {
                "expected": expected,
                "recognized": actual,
                "character_error_rate": distance(expected, actual) / len(expected),
                "word_error_rate": distance(expected.split(), actual.split())
                / len(expected.split()),
                "seconds": round(time.monotonic() - started, 3),
                "status": page["processing_status"],
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "scope": (
                    "Two clean synthetic English scans; "
                    "not confidence calibration or production accuracy"
                ),
                "normalization": (
                    "Whitespace collapsed for CER/WER; case, punctuation and amounts retained"
                ),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "parser_version": PARSER_VERSION,
                "ocr_identity": identity,
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Recorded actual OCR fixture metrics: {args.output}")


if __name__ == "__main__":
    main()
