"""Measure native/scan extraction and deterministic comparison on separate synthetic gold."""

import argparse
import os
import platform
from collections import Counter
from decimal import Decimal
from pathlib import Path
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

import pymupdf
from documind.comparison import compare_records, decimal_string, extract_record
from documind.ocr import model_identity
from documind.parser import extract_pdf
from documind.reflection import canonical, digest

CONFIG = {
    "max_pdf_pages": 4,
    "max_page_pixels": 4_000_000,
    "max_document_pixels": 8_000_000,
    "max_rendered_bytes": 8_000_000,
    "max_extracted_chars": 10_000,
    "tables_enabled": True,
}
COMPARISONS = (
    ("positive", "GBP", "GBP", "1988.28", "1987.27", True, True, "AMOUNT_MISMATCH", "1.01"),
    ("equal", "USD", "USD", "4567.01", "4567.01", True, True, "AMOUNT_MATCH", "0.00"),
    (
        "creditless-negative",
        "CHF",
        "CHF",
        "187.88",
        "247.93",
        True,
        True,
        "AMOUNT_MISMATCH",
        "-60.05",
    ),
    ("different-po", "CAD", "CAD", "31.05", "19.05", False, True, "UNVERIFIED_MATCH", None),
    ("different-currency", "EUR", "AED", "330.15", "330.15", True, True, "UNVERIFIED_TOTAL", None),
    ("different-basis", "JPY", "JPY", "1350", "1140", True, False, "UNVERIFIED_TOTAL", None),
)
SCAN_TEXT = (
    "Reference: Archive 7042\nBalance due: GBP 481.20\nThank you for your payment",
    "Reference: Clearing 2189\nBalance due: CAD 517.49\nPlease retain this statement",
    "Reference: Accounts 1634\nBalance due: CHF 762.05\nKeep a copy for your records",
)
TABLE_CELLS = (("Item", "Quantity"), ("Pens", "14"), ("Notebooks", "21"))


def pdf(text=None, *, scan=False, rotate=0):
    with pymupdf.open() as source:
        page = source.new_page(width=420, height=340)
        if text is not None:
            page.insert_text((35, 60), text, fontsize=14)
        else:
            for x in (35, 185, 335):
                page.draw_line((x, 50), (x, 185))
            for n in range(4):
                y = 50 + n * 45
                page.draw_line((35, y), (335, y))
            for row, cells in enumerate(TABLE_CELLS):
                for column, value in enumerate(cells):
                    page.insert_text((50 + column * 150, 79 + row * 45), value, fontsize=15)
        if not scan:
            page.set_rotation(rotate)
            return source.tobytes(no_new_id=True)
        raster = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
        with pymupdf.open() as scanned:
            scanned_page = scanned.new_page(width=420, height=340)
            scanned_page.insert_image(scanned_page.rect, stream=raster)
            scanned_page.set_rotation(rotate)
            return scanned.tobytes(no_new_id=True)


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        previous, row = row, [i]
        for j, right in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[-1] + 1, previous[j - 1] + (left != right)))
    return row[-1]


def measure(output, ocr_root):
    output.mkdir(mode=0o700)
    ocr = dict(
        CONFIG,
        ocr_enabled=True,
        ocr_dpi=144,
        ocr_model_root=str(ocr_root),
        ocr_identity=model_identity(ocr_root),
    )
    comparisons, extraction = [], []
    source_hashes = {}

    def run(name, raw, config):
        (output / (name + ".pdf")).write_bytes(raw)
        source_hashes[name] = digest(raw)
        start = monotonic()
        (page,) = extract_pdf(raw, config, 90)
        return page, monotonic() - start

    (output / "gold.json").write_text(
        canonical(
            {
                "source": "project-authored synthetic",
                "comparisons": COMPARISONS,
                "scan_text": SCAN_TEXT,
                "table_cells": TABLE_CELLS,
            }
        )
        + "\n"
    )
    for index, gold in enumerate(SCAN_TEXT, 1):
        name = f"scan-{index}"
        page, elapsed = run(name, pdf(gold, scan=True, rotate=90 if index == 2 else 0), ocr)
        line = " ".join(page["text_raw"].split())
        expected = " ".join(gold.split())
        extraction.append(
            {
                "id": name,
                "track": "ocr",
                "seconds": elapsed,
                "actual": line,
                "char_errors": distance(expected, line),
                "chars": len(expected),
                "word_errors": distance(expected.split(), line.split()),
                "words": len(expected.split()),
            }
        )
    for scan in (False, True):
        name = "scanned-table" if scan else "native-table"
        page, elapsed = run(name, pdf(scan=scan), ocr if scan else CONFIG)
        actual = {}
        for element in page["elements"]:
            if element["element_type"] == "table_cell":
                box = element["table_coordinates"]
                actual[box["row_index"], box["column_index"]] = element["text_raw"]
        expected = {
            (r, c): text for r, cells in enumerate(TABLE_CELLS) for c, text in enumerate(cells)
        }
        extraction.append(
            {
                "id": name,
                "track": "table",
                "seconds": elapsed,
                "correct_cells": sum(actual.get(key) == text for key, text in expected.items()),
                "expected_cells": len(expected),
                "actual_cells": len(actual),
                "structure_exact": expected.keys() == actual.keys(),
                "actual": [[r, c, text] for (r, c), text in sorted(actual.items())],
            }
        )
    for index, (
        name,
        left_c,
        right_c,
        left_n,
        right_n,
        link,
        basis,
        label,
        difference,
    ) in enumerate(COMPARISONS, 1):
        left_id, right_id = f"AP8-{index:02}", f"AP8-{index:02}" if link else f"AP8-{index:02}-ADJ"
        vendor = f"SHIP-{9000 + index}"
        records, scores = [], []
        for side, po, currency, amount, tax in (
            ("invoice", left_id, left_c, left_n, "tax-inclusive"),
            (
                "purchase_order",
                right_id,
                right_c,
                right_n,
                "tax-inclusive" if basis else "tax-exclusive",
            ),
        ):
            document = f"{name}-{side}"
            raw = f"Document type: {side.replace('_', ' ').title()}\n"
            raw += f"Invoice number: EV-{index:03}\n" if side == "invoice" else ""
            raw += f"PO number: {po}\nVendor ID: {vendor}\nGrand total: {currency} {amount}\n"
            raw += f"Grand total basis: {tax}"
            page, elapsed = run(document, pdf(raw), CONFIG)
            identifier = uuid5(NAMESPACE_URL, document)
            elements = [
                {**row, "page_id": identifier, "id": uuid5(identifier, str(j))}
                for j, row in enumerate(page["elements"])
            ]
            record = extract_record(page["processing_status"], elements)
            records.append({**vars(record), "document_id": identifier})
            gold = {
                "kind": side,
                "po_number": po,
                "vendor_id": vendor,
                "currency": currency,
                "grand_total": Decimal(amount),
                "total_basis": tax,
            }
            scores.append(
                {
                    "document": document,
                    "correct_fields": sum(getattr(record, k) == v for k, v in gold.items()),
                    "predicted_fields": sum(getattr(record, k) is not None for k in gold),
                    "gold_fields": len(gold),
                    "seconds": elapsed,
                }
            )
        decision = compare_records(records)
        actual = decimal_string(decision.difference) if decision.difference is not None else None
        comparisons.append(
            {
                "id": name,
                "expected_type": label,
                "actual_type": decision.type,
                "reason": decision.reason,
                "expected_difference": difference,
                "actual_difference": actual,
                "correct": label == decision.type and difference == actual,
                "fields": scores,
            }
        )
    confusion = Counter(
        (row["expected_type"] == "AMOUNT_MISMATCH", row["actual_type"] == "AMOUNT_MISMATCH")
        for row in comparisons
    )
    return {
        "dataset": "phase8-documents-v1",
        "host": platform.platform(),
        "ocr": ocr["ocr_identity"],
        "source_pdf_sha256": source_hashes,
        "extraction": extraction,
        "comparisons": comparisons,
        "discrepancy_tp": confusion[True, True],
        "discrepancy_fp": confusion[False, True],
        "discrepancy_fn": confusion[True, False],
        "discrepancy_tn": confusion[False, False],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ocr-root", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = measure(args.output, args.ocr_root.resolve())
    (args.output / "results.json").write_text(canonical(result) + "\n")
    print(
        f"Measured {len(result['extraction'])} extraction fixtures and "
        f"{len(result['comparisons'])} comparison pairs"
    )


if __name__ == "__main__":
    main()
