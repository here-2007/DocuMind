"""Private local object store. Shared named volume in Compose; no public URLs."""

import os
import tempfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from documind.config import settings


class ObjectStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or settings().object_store_root).resolve()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, key: str) -> Path:
        path = self.root / key
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise ValueError("Invalid object key")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Object path escapes private storage")
        return path

    def put(self, key: str, data: bytes, media_type: str):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            # Link atomically without overwriting an immutable existing artifact.
            try:
                os.link(name, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError("Immutable object content conflict") from None
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(name)

    def get(self, key: str, max_bytes: int) -> bytes:
        with self.path(key).open("rb") as f:
            data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Object exceeds configured limit")
        return data

    def keys(self, prefix: str = ""):
        base = self.path(prefix) if prefix else self.root
        if not base.exists():
            return
        for path in base.rglob("*"):
            if path.is_file() and not path.is_symlink():
                yield {
                    "Key": path.relative_to(self.root).as_posix(),
                    "LastModified": datetime.fromtimestamp(path.stat().st_mtime, UTC),
                }

    def delete(self, key: str):
        self.path(key).unlink(missing_ok=True)


@lru_cache
def store() -> ObjectStore:
    return ObjectStore()
