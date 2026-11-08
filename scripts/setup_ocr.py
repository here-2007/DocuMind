"""Download the fixed public CPU OCR profile once; inference never downloads weights."""

import hashlib
import json
from pathlib import Path

from documind.ocr import MODELS, PACKAGES


def main():
    import argparse
    from importlib.metadata import version

    from huggingface_hub import HfApi, snapshot_download

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".data/models/ocr"))
    args = parser.parse_args()
    root = args.root.resolve()
    if root.exists():
        raise SystemExit("Refusing to overwrite OCR artifacts; choose a new model root")
    if {name: version(name) for name in PACKAGES} != PACKAGES:
        raise SystemExit("Install the locked OCR extra first")
    revisions = {}
    for kind, name in MODELS.items():
        repo_id = "PaddlePaddle/" + name
        revision = HfApi().model_info(repo_id).sha
        revisions[repo_id] = revision
        snapshot_download(
            repo_id,
            revision=revision,
            local_dir=root / kind,
            allow_patterns=["inference.json", "inference.pdiparams", "inference.yml"],
        )
    files = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.glob("*/*"))
        if p.is_file()
    }
    manifest = {"models": MODELS, "packages": PACKAGES, "revisions": revisions, "files": files}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    from documind.ocr import model_identity

    model_identity(root)
    print(f"Verified local OCR artifacts: {root}")


if __name__ == "__main__":
    main()
