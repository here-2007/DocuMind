"""Private local object store. Shared named volume in Compose; no public URLs."""

import hashlib
import math
import os
import tempfile
import zlib
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from documind.config import settings
from documind.errors import AppError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def valid_legacy_png(data):
    if not data.startswith(PNG_SIGNATURE):
        return False
    offset = len(PNG_SIGNATURE)
    view = memoryview(data)
    while offset + 12 <= len(data):
        length = int.from_bytes(view[offset : offset + 4], "big")
        end = offset + 8 + length
        if end + 4 > len(data) or zlib.crc32(view[offset + 4 : end]) != int.from_bytes(
            view[end : end + 4], "big"
        ):
            return False
        kind = data[offset + 4 : offset + 8]
        if offset == len(PNG_SIGNATURE) and (kind != b"IHDR" or length != 13):
            return False
        if kind == b"IEND":
            return length == 0 and end + 4 == len(data)
        offset = end + 4
    return False


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

    def source_size(self, key, *, sha256=None):
        try:
            path = self.path(key)
            if not path.is_file():
                raise FileNotFoundError
            if sha256 is None:
                return path.stat().st_size
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                if hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                    raise AppError("SOURCE_HASH_MISMATCH", 503)
                return size
        except (OSError, ValueError) as exc:
            raise AppError("SOURCE_ARTIFACT_UNAVAILABLE", 503) from exc

    def read_source(self, key, *, size, sha256, legacy_preview_limit=None):
        try:
            with self.path(key).open("rb") as stream:
                if size is None:
                    # Older originals lack a size receipt. Verify before allocating their bytes.
                    size = os.fstat(stream.fileno()).st_size
                    if legacy_preview_limit is not None and size > legacy_preview_limit:
                        raise AppError("SOURCE_HASH_MISMATCH", 503)
                    if sha256 and hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                        raise AppError("SOURCE_HASH_MISMATCH", 503)
                    stream.seek(0)
                data = stream.read(size + 1)
        except (OSError, ValueError) as exc:
            raise AppError("SOURCE_ARTIFACT_UNAVAILABLE", 503) from exc
        if len(data) != size or (sha256 and hashlib.sha256(data).hexdigest() != sha256):
            raise AppError("SOURCE_HASH_MISMATCH", 503)
        if legacy_preview_limit is not None and not sha256 and not valid_legacy_png(data):
            raise AppError("SOURCE_HASH_MISMATCH", 503)
        return data

    def read_preview(self, page):
        return self.read_source(
            page["render_object_key"],
            size=page["render_size_bytes"],
            sha256=page["render_sha256"],
            legacy_preview_limit=math.ceil(page["width"]) * math.ceil(page["height"]) * 4 + 1000000,
        )

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
