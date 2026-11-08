"""Optional, local-only PaddleOCR with content-addressed model provenance."""

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from documind.errors import AppError

MODELS = {"det": "PP-OCRv5_mobile_det", "rec": "en_PP-OCRv5_mobile_rec"}
PACKAGES = {"paddleocr": "3.7.0", "paddlepaddle": "3.3.1"}


def model_identity(root: Path) -> dict:
    try:
        actual = {name: version(name) for name in PACKAGES}
        if actual != PACKAGES:
            raise ValueError("Unsupported OCR packages")
        manifest = json.loads((root / "manifest.json").read_text())
        if manifest["models"] != MODELS or manifest["packages"] != PACKAGES:
            raise ValueError("Unsupported OCR profile")
        for name, digest in manifest["files"].items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("Missing OCR artifact")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Changed OCR artifact")
        for kind in MODELS:
            for required in ("inference.json", "inference.pdiparams", "inference.yml"):
                if f"{kind}/{required}" not in manifest["files"]:
                    raise ValueError("Incomplete OCR manifest")
        return manifest
    except (OSError, ValueError, KeyError, PackageNotFoundError) as exc:
        raise AppError("OCR_MODEL_UNAVAILABLE", 503) from exc


def load_engine(root: Path, identity: dict):
    if model_identity(root) != identity:
        raise AppError("OCR_MODEL_IDENTITY_MISMATCH", 503)
    try:
        from paddleocr import PaddleOCR

        return PaddleOCR(
            text_detection_model_name=MODELS["det"],
            text_detection_model_dir=str(root / "det"),
            text_recognition_model_name=MODELS["rec"],
            text_recognition_model_dir=str(root / "rec"),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            cpu_threads=2,
            enable_mkldnn=False,
        )
    except Exception as exc:
        raise AppError("OCR_RUNTIME_UNAVAILABLE", 503) from exc


def recognize(engine, image: Path, width: int, height: int) -> list[dict]:
    import math

    try:
        result = list(engine.predict(str(image), text_rec_score_thresh=0.0))[0]
        lines = []
        texts, scores, polygons = result["rec_texts"], result["rec_scores"], result["rec_polys"]
        if not len(texts) == len(scores) == len(polygons):
            raise ValueError("Mismatched OCR arrays")
        for text, score, polygon in zip(texts, scores, polygons, strict=True):
            if not text.strip():
                continue
            score = float(score)
            points = [[float(x), float(y)] for x, y in polygon]
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Invalid OCR confidence")
            if not all(math.isfinite(v) for point in points for v in point):
                raise ValueError("Invalid OCR geometry")
            xs, ys = zip(*points, strict=True)
            box = [
                max(0, min(xs) / width),
                max(0, min(ys) / height),
                min(1, max(xs) / width),
                min(1, max(ys) / height),
            ]
            if box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError("Empty OCR geometry")
            lines.append(
                {"text_raw": text, "bbox_norm": box, "source_type": "ocr", "confidence": score}
            )
        return sorted(lines, key=lambda line: (line["bbox_norm"][1], line["bbox_norm"][0]))
    except Exception as exc:
        raise AppError("OCR_EXTRACTION_FAILED", 422) from exc
