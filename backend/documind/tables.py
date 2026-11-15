"""Bounded ruled-table adapters; uncertain structure always requires review."""

from documind.errors import AppError


def native_tables(page, max_cells):
    tables = []
    for table in page.find_tables(strategy="lines_strict").tables:
        if table.row_count * table.col_count > max_cells:
            raise AppError("TABLE_CELL_LIMIT", 422)
        values = table.extract()
        cells = []
        boxes = [box for row in table.rows for box in row.cells if box is not None]
        xs = sorted({round(box[i], 3) for box in boxes for i in (0, 2)})
        ys = sorted({round(box[i], 3) for box in boxes for i in (1, 3)})
        seen = set()
        for row, cells_row in enumerate(table.rows):
            for column, box in enumerate(cells_row.cells):
                if box is None or tuple(box) in seen:
                    continue
                seen.add(tuple(box))
                cells.append(
                    {
                        "text_raw": values[row][column] or "",
                        "box": box,
                        "row_index": row,
                        "column_index": column,
                        "row_span": sum(box[1] - 0.002 <= y < box[3] - 0.002 for y in ys),
                        "column_span": sum(box[0] - 0.002 <= x < box[2] - 0.002 for x in xs),
                        "is_header": row == 0 if not table.header.external else None,
                        "confidence": None,
                    }
                )
        if cells:
            tables.append(
                {
                    "box": table.bbox,
                    "cells": cells,
                    "rows": table.row_count,
                    "columns": table.col_count,
                    "headers": table.header.names,
                    "extractor": "pymupdf-lines-strict",
                    "source_type": "native_text",
                }
            )
    return tables


def raster_tables(image, lines, max_cells):
    import cv2

    gray = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    height, width = gray.shape
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, width // 20), 1))
    )
    vertical = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 20))),
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(horizontal | vertical)
    tables = []
    assigned = set()
    for index in range(1, count):
        x, y, w, h, _ = stats[index]
        if w < width * 0.15 or h < height * 0.04:
            continue
        xs = _centers((vertical[y : y + h, x : x + w] > 0).sum(axis=0), h * 0.85)
        ys = _centers((horizontal[y : y + h, x : x + w] > 0).sum(axis=1), w * 0.85)
        if len(xs) < 2 or len(ys) < 2 or (len(xs) == 2 and len(ys) == 2):
            continue
        if (len(xs) - 1) * (len(ys) - 1) > max_cells:
            raise AppError("TABLE_CELL_LIMIT", 422)
        cells = []
        for row, (top, bottom) in enumerate(zip(ys, ys[1:], strict=False)):
            for column, (left, right) in enumerate(zip(xs, xs[1:], strict=False)):
                box = [
                    (x + left) / width,
                    (y + top) / height,
                    (x + right) / width,
                    (y + bottom) / height,
                ]
                indices = [
                    i
                    for i, line in enumerate(lines)
                    if i not in assigned and contains(box, line["bbox_norm"])
                ]
                assigned.update(indices)
                matches = [lines[i] for i in indices]
                cells.append(
                    {
                        "text_raw": "\n".join(line["text_raw"] for line in matches),
                        "box": box,
                        "row_index": row,
                        "column_index": column,
                        "is_header": None,
                        "confidence": min((line["confidence"] for line in matches), default=None),
                    }
                )
        tables.append(
            {
                "box": [
                    (x + xs[0]) / width,
                    (y + ys[0]) / height,
                    (x + xs[-1]) / width,
                    (y + ys[-1]) / height,
                ],
                "cells": cells,
                "rows": len(ys) - 1,
                "columns": len(xs) - 1,
                "headers": None,
                "extractor": "opencv-ruled-grid-v1",
                "source_type": "ocr",
            }
        )
    return tables, [line for i, line in enumerate(lines) if i not in assigned]


def _centers(values, threshold):
    groups = []
    for index, value in enumerate(values):
        if value >= threshold:
            if groups and index == groups[-1][-1] + 1:
                groups[-1].append(index)
            else:
                groups.append([index])
    return [sum(group) / len(group) for group in groups]


def contains(outer, inner):
    x, y = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]
