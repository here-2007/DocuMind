import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from documind import schema as t
from documind.config import Settings
from documind.errors import AppError
from documind.parser import PARSER_VERSION, REVIEW_VERSION, fingerprint
from documind.storage import ObjectStore


def now():
    return datetime.now(UTC)


class Repository:
    def __init__(self, engine: Engine, objects: ObjectStore, cfg: Settings):
        self.engine, self.objects, self.cfg = engine, objects, cfg

    def initialize(self):
        from sqlalchemy.dialects.postgresql import insert

        self.objects.initialize()
        with self.engine.begin() as c:
            c.execute(
                insert(t.workspaces)
                .values(id=self.cfg.local_workspace_id, name="Local workspace")
                .on_conflict_do_nothing()
            )

    def authorize(self, workspace_id: UUID):
        if workspace_id != self.cfg.local_workspace_id:
            raise AppError("NOT_FOUND", 404)

    def parser_config(self):
        config = {
            "max_pdf_pages": self.cfg.max_pdf_pages,
            "max_page_pixels": self.cfg.max_page_pixels,
            "max_document_pixels": self.cfg.max_document_pixels,
            "max_rendered_bytes": self.cfg.max_rendered_bytes,
            "max_extracted_chars": self.cfg.max_extracted_chars,
            "tables_enabled": self.cfg.tables_enabled,
            "max_table_cells": self.cfg.max_table_cells,
            "ocr_enabled": self.cfg.ocr_enabled,
            "ocr_dpi": self.cfg.ocr_dpi,
            "ocr_min_native_chars": self.cfg.ocr_min_native_chars,
        }
        if self.cfg.ocr_enabled:
            from documind.ocr import model_identity

            config["ocr_model_root"] = str(self.cfg.ocr_model_root.resolve())
            config["ocr_identity"] = model_identity(self.cfg.ocr_model_root)
        return config

    def upload(self, workspace_id: UUID, data: bytes, filename: str, key: str):
        self.authorize(workspace_id)
        if not data.startswith(b"%PDF-"):
            raise AppError("INVALID_DOCUMENT")
        digest = hashlib.sha256(data).hexdigest()
        request_hash = fingerprint({"sha256": digest, "filename": filename})
        document_id, job_id = uuid4(), uuid4()
        config = self.parser_config()
        version = fingerprint(
            {
                "document_id": str(document_id),
                "sha256": digest,
                "parser": PARSER_VERSION,
                "config": config,
            }
        )
        object_key = f"{workspace_id}/{document_id}/original.pdf"
        # Store before acknowledging upload; a failed commit leaves a collectable orphan.
        with self.engine.begin() as c:
            c.execute(
                sa.select(t.workspaces).where(t.workspaces.c.id == workspace_id).with_for_update()
            )
            previous = (
                c.execute(
                    sa.select(t.request_keys).where(
                        t.request_keys.c.workspace_id == workspace_id,
                        t.request_keys.c.principal_id == workspace_id,
                        t.request_keys.c.operation == "upload",
                        t.request_keys.c.key == key,
                    )
                )
                .mappings()
                .first()
            )
            if previous:
                if previous["request_hash"] != request_hash:
                    raise AppError("IDEMPOTENCY_CONFLICT", 409)
                return self._summary(c, workspace_id, previous["resource_id"], previous["job_id"])
            self.objects.put(object_key, data, "application/pdf")
            c.execute(
                t.documents.insert().values(
                    id=document_id,
                    workspace_id=workspace_id,
                    filename=filename,
                    media_type="application/pdf",
                    sha256=digest,
                    object_key=object_key,
                    latest_ingestion_version=version,
                )
            )
            c.execute(
                t.versions.insert().values(
                    workspace_id=workspace_id,
                    document_id=document_id,
                    ingestion_version=version,
                    parser_version=PARSER_VERSION,
                    config_hash=fingerprint(config),
                    config_json=config,
                )
            )
            c.execute(
                t.jobs.insert().values(
                    id=job_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    ingestion_version=version,
                    kind="ingest",
                    idempotency_key=key,
                    next_attempt_at=now(),
                )
            )
            c.execute(
                t.request_keys.insert().values(
                    workspace_id=workspace_id,
                    principal_id=workspace_id,
                    operation="upload",
                    key=key,
                    request_hash=request_hash,
                    resource_id=document_id,
                    job_id=job_id,
                )
            )
            return self._summary(c, workspace_id, document_id, job_id)

    def _document(self, c, workspace_id, document_id, *, lock=False):
        query = sa.select(t.documents).where(
            t.documents.c.workspace_id == workspace_id, t.documents.c.id == document_id
        )
        if lock:
            query = query.with_for_update()
        doc = c.execute(query).mappings().first()
        if not doc:
            raise AppError("NOT_FOUND", 404)
        if doc["lifecycle_status"] != "active":
            raise AppError("SOURCE_UNAVAILABLE", 410)
        return doc

    def _summary(self, c, workspace_id, document_id, job_id=None):
        d = dict(self._document(c, workspace_id, document_id))
        v = (
            c.execute(
                sa.select(t.versions).where(
                    t.versions.c.workspace_id == workspace_id,
                    t.versions.c.document_id == document_id,
                    t.versions.c.ingestion_version == d["latest_ingestion_version"],
                )
            )
            .mappings()
            .one()
        )
        if job_id is None:
            job_id = c.execute(
                sa.select(t.jobs.c.id).where(
                    t.jobs.c.workspace_id == workspace_id,
                    t.jobs.c.document_id == document_id,
                    t.jobs.c.kind == "ingest",
                    t.jobs.c.ingestion_version == d["latest_ingestion_version"],
                )
            ).scalar_one()
        return {
            "document_id": document_id,
            "job_id": job_id,
            "filename": d["filename"],
            "created_at": d["created_at"],
            "lifecycle_status": d["lifecycle_status"],
            "latest_ingestion_version": d["latest_ingestion_version"],
            "preview_ingestion_version": d["preview_ingestion_version"],
            "published_ingestion_version": None,
            "extraction_status": v["extraction_status"],
            "index_status": v["index_status"],
            "error_code": v["error_code"],
            "preview_available": d["preview_ingestion_version"] is not None,
            "searchable": False,
        }

    def summary(self, workspace_id, document_id):
        self.authorize(workspace_id)
        with self.engine.connect() as c:
            return self._summary(c, workspace_id, document_id)

    def list_documents(self, workspace_id, limit, after=None):
        self.authorize(workspace_id)
        with self.engine.connect() as c:
            q = sa.select(t.documents.c.id).where(
                t.documents.c.workspace_id == workspace_id,
                t.documents.c.lifecycle_status == "active",
            )
            if after:
                q = q.where(t.documents.c.id > after)
            ids = c.execute(q.order_by(t.documents.c.id).limit(limit + 1)).scalars().all()
            return [self._summary(c, workspace_id, x) for x in ids[:limit]], ids[limit:]

    def source(self, workspace_id, document_id, version=None, page_number=None):
        self.authorize(workspace_id)
        with self.engine.connect() as c:
            d = self._document(c, workspace_id, document_id)
            version = version or d["preview_ingestion_version"]
            if not version:
                raise AppError("DOCUMENT_NOT_READY", 409)
            visible = c.execute(
                sa.select(t.versions.c.extraction_status).where(
                    t.versions.c.workspace_id == workspace_id,
                    t.versions.c.document_id == document_id,
                    t.versions.c.ingestion_version == version,
                )
            ).scalar_one_or_none()
            if visible not in {"complete", "needs_review"}:
                raise AppError("NOT_FOUND", 404)
            q = sa.select(t.pages).where(
                t.pages.c.workspace_id == workspace_id,
                t.pages.c.document_id == document_id,
                t.pages.c.ingestion_version == version,
            )
            if page_number is not None:
                q = q.where(t.pages.c.page_number == page_number)
            rows = [dict(x) for x in c.execute(q.order_by(t.pages.c.page_number)).mappings()]
            self._document(c, workspace_id, document_id)
            return rows

    def element_list(
        self, workspace_id, document_id, version, page_number, limit, after=None, element_type=None
    ):
        visible_pages = self.source(workspace_id, document_id, version, page_number)
        if not visible_pages:
            raise AppError("NOT_FOUND", 404)
        with self.engine.connect() as c:
            q = sa.select(t.elements).where(
                t.elements.c.workspace_id == workspace_id,
                t.elements.c.document_id == document_id,
                t.elements.c.page_id.in_([p["id"] for p in visible_pages]),
            )
            if after:
                q = q.where(t.elements.c.id > after)
            if element_type:
                q = q.where(t.elements.c.element_type == element_type)
            rows = [
                dict(x) for x in c.execute(q.order_by(t.elements.c.id).limit(limit + 1)).mappings()
            ]
            self._document(c, workspace_id, document_id)
            return rows[:limit], rows[limit:]

    def job(self, workspace_id, job_id):
        self.authorize(workspace_id)
        with self.engine.connect() as c:
            job = (
                c.execute(
                    sa.select(t.jobs).where(
                        t.jobs.c.workspace_id == workspace_id, t.jobs.c.id == job_id
                    )
                )
                .mappings()
                .first()
            )
            if not job:
                raise AppError("NOT_FOUND", 404)
            return {k: v for k, v in job.items() if k not in {"lease_token", "idempotency_key"}}

    def _request(self, c, workspace_id, operation, key, body):
        c.execute(
            sa.select(t.workspaces).where(t.workspaces.c.id == workspace_id).with_for_update()
        )
        digest = fingerprint(body)
        previous = (
            c.execute(
                sa.select(t.request_keys).where(
                    t.request_keys.c.workspace_id == workspace_id,
                    t.request_keys.c.principal_id == workspace_id,
                    t.request_keys.c.operation == operation,
                    t.request_keys.c.key == key,
                )
            )
            .mappings()
            .first()
        )
        if previous and previous["request_hash"] != digest:
            raise AppError("IDEMPOTENCY_CONFLICT", 409)
        return previous, digest

    def retry(self, workspace_id, job_id, key):
        self.authorize(workspace_id)
        with self.engine.begin() as c:
            previous, digest = self._request(c, workspace_id, "retry", key, {"job_id": str(job_id)})
            j = (
                c.execute(
                    sa.select(t.jobs).where(
                        t.jobs.c.workspace_id == workspace_id, t.jobs.c.id == job_id
                    )
                )
                .mappings()
                .first()
            )
            if not j:
                raise AppError("NOT_FOUND", 404)
            if j["kind"] == "delete":
                c.execute(
                    sa.select(t.documents)
                    .where(
                        t.documents.c.workspace_id == workspace_id,
                        t.documents.c.id == j["document_id"],
                    )
                    .with_for_update()
                ).one()
            else:
                self._document(c, workspace_id, j["document_id"], lock=True)
            j = (
                c.execute(sa.select(t.jobs).where(t.jobs.c.id == job_id).with_for_update())
                .mappings()
                .one()
            )
            if not previous:
                if j["status"] == "failed":
                    c.execute(
                        t.jobs.update()
                        .where(t.jobs.c.id == job_id)
                        .values(
                            status="queued",
                            attempts=0,
                            next_attempt_at=now(),
                            error_code=None,
                            finished_at=None,
                        )
                    )
                    if j["kind"] == "ingest":
                        c.execute(
                            t.versions.update()
                            .where(
                                t.versions.c.workspace_id == workspace_id,
                                t.versions.c.document_id == j["document_id"],
                                t.versions.c.ingestion_version == j["ingestion_version"],
                            )
                            .values(extraction_status="queued", error_code=None)
                        )
                c.execute(
                    t.request_keys.insert().values(
                        workspace_id=workspace_id,
                        principal_id=workspace_id,
                        operation="retry",
                        key=key,
                        request_hash=digest,
                        resource_id=j["document_id"],
                        job_id=job_id,
                    )
                )
        return self.job(workspace_id, job_id)

    def reprocess(self, workspace_id, document_id, key, *, review=None):
        self.authorize(workspace_id)
        operation = "review" if review is not None else "reprocess"
        parser_version = REVIEW_VERSION if review is not None else PARSER_VERSION
        payload = {"document_id": str(document_id)}
        if review is not None:
            payload["review"] = review
        with self.engine.begin() as c:
            previous, digest = self._request(c, workspace_id, operation, key, payload)
            d = self._document(c, workspace_id, document_id, lock=True)
            if previous:
                return self._summary(c, workspace_id, document_id, previous["job_id"])
            if review is not None:
                parent = review["base_ingestion_version"]
                state = c.execute(
                    sa.select(t.versions.c.extraction_status).where(
                        t.versions.c.workspace_id == workspace_id,
                        t.versions.c.document_id == document_id,
                        t.versions.c.ingestion_version == parent,
                    )
                ).scalar_one_or_none()
                if state not in {"complete", "needs_review"}:
                    raise AppError("INVALID_REVIEW_SOURCE", 409)
                candidates = {
                    row["id"]: row["element_type"]
                    for row in c.execute(
                        sa.select(t.elements.c.id, t.elements.c.element_type).where(
                            t.elements.c.workspace_id == workspace_id,
                            t.elements.c.document_id == document_id,
                            t.elements.c.ingestion_version == parent,
                            t.elements.c.element_type != "table",
                        )
                    ).mappings()
                }
                corrections = review["corrections"]
                if len({item["element_id"] for item in corrections}) != len(corrections):
                    raise AppError("INVALID_CORRECTIONS", 422)
                for item in corrections:
                    element_type = candidates.get(UUID(item["element_id"]))
                    if element_type is None or "\x00" in item["text_raw"]:
                        raise AppError("INVALID_CORRECTIONS", 422)
                    if element_type != "table_cell" and (
                        not item["text_raw"].strip() or item["is_header"] is not None
                    ):
                        raise AppError("INVALID_CORRECTIONS", 422)
                config = {
                    "review": review,
                    "reviewer_id": str(workspace_id),
                    "max_extracted_chars": self.cfg.max_extracted_chars,
                }
            else:
                config = self.parser_config()
            version = fingerprint(
                {
                    "document_id": str(document_id),
                    "sha256": d["sha256"],
                    "parser": parser_version,
                    "config": config,
                }
            )
            existing = c.execute(
                sa.select(t.jobs.c.id).where(
                    t.jobs.c.document_id == document_id,
                    t.jobs.c.ingestion_version == version,
                    t.jobs.c.kind == "ingest",
                )
            ).scalar_one_or_none()
            job_id = existing or uuid4()
            if not existing:
                c.execute(
                    t.versions.insert().values(
                        workspace_id=workspace_id,
                        document_id=document_id,
                        ingestion_version=version,
                        parser_version=parser_version,
                        config_hash=fingerprint(config),
                        config_json=config,
                    )
                )
                c.execute(
                    t.jobs.insert().values(
                        id=job_id,
                        workspace_id=workspace_id,
                        document_id=document_id,
                        ingestion_version=version,
                        kind="ingest",
                        idempotency_key=f"{operation}:{version}",
                        next_attempt_at=now(),
                    )
                )
            c.execute(
                t.documents.update()
                .where(t.documents.c.id == document_id)
                .values(latest_ingestion_version=version)
            )
            c.execute(
                t.request_keys.insert().values(
                    workspace_id=workspace_id,
                    principal_id=workspace_id,
                    operation=operation,
                    key=key,
                    request_hash=digest,
                    resource_id=document_id,
                    job_id=job_id,
                )
            )
            return self._summary(c, workspace_id, document_id, job_id)

    def delete(self, workspace_id, document_id):
        self.authorize(workspace_id)
        with self.engine.begin() as c:
            d = (
                c.execute(
                    sa.select(t.documents)
                    .where(
                        t.documents.c.id == document_id, t.documents.c.workspace_id == workspace_id
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not d:
                raise AppError("NOT_FOUND", 404)
            existing = c.execute(
                sa.select(t.jobs.c.id).where(
                    t.jobs.c.workspace_id == workspace_id,
                    t.jobs.c.document_id == document_id,
                    t.jobs.c.kind == "delete",
                )
            ).scalar_one_or_none()
            if existing:
                return existing
            job_id = uuid4()
            c.execute(
                t.documents.update()
                .where(t.documents.c.id == document_id)
                .values(lifecycle_status="deleting")
            )
            c.execute(
                t.jobs.update()
                .where(
                    t.jobs.c.document_id == document_id, t.jobs.c.status.in_(["queued", "running"])
                )
                .values(
                    status="cancelled", lease_token=None, lease_expires_at=None, finished_at=now()
                )
            )
            c.execute(
                t.jobs.insert().values(
                    id=job_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    ingestion_version=d["latest_ingestion_version"],
                    kind="delete",
                    idempotency_key=f"delete:{document_id}",
                    next_attempt_at=now(),
                )
            )
            return job_id

    def claim(self, job_id):
        with self.engine.begin() as c:
            j = c.execute(sa.select(t.jobs).where(t.jobs.c.id == job_id)).mappings().first()
            if not j:
                return None
            d = (
                c.execute(
                    sa.select(t.documents)
                    .where(t.documents.c.id == j["document_id"])
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            j = (
                c.execute(sa.select(t.jobs).where(t.jobs.c.id == job_id).with_for_update())
                .mappings()
                .one()
            )
            if j["status"] != "queued" or j["next_attempt_at"] > now():
                return None
            if j["kind"] == "ingest" and d["lifecycle_status"] != "active":
                return None
            token = uuid4()
            c.execute(
                t.jobs.update()
                .where(t.jobs.c.id == job_id)
                .values(
                    status="running",
                    attempts=j["attempts"] + 1,
                    lease_token=token,
                    lease_expires_at=now() + timedelta(seconds=self.cfg.job_lease_seconds),
                    started_at=now(),
                )
            )
            if j["kind"] == "ingest":
                c.execute(
                    t.versions.update()
                    .where(
                        t.versions.c.workspace_id == j["workspace_id"],
                        t.versions.c.document_id == j["document_id"],
                        t.versions.c.ingestion_version == j["ingestion_version"],
                    )
                    .values(extraction_status="processing", error_code=None)
                )
            return {
                **dict(j),
                "lease_token": token,
                "attempts": j["attempts"] + 1,
                "object_key": d["object_key"],
                "sha256": d["sha256"],
            }

    def heartbeat(self, job):
        with self.engine.begin() as c:
            result = c.execute(
                t.jobs.update()
                .where(
                    t.jobs.c.id == job["id"],
                    t.jobs.c.status == "running",
                    t.jobs.c.lease_token == job["lease_token"],
                    t.jobs.c.lease_expires_at > now(),
                )
                .values(lease_expires_at=now() + timedelta(seconds=self.cfg.job_lease_seconds))
            )
            if result.rowcount != 1:
                raise AppError("LEASE_LOST", 409)

    def promote(self, job, parsed):
        workspace_id, document_id, version = (
            job["workspace_id"],
            job["document_id"],
            job["ingestion_version"],
        )
        staged = []
        hashes = []
        for page in parsed:
            self.heartbeat(job)
            page_id = uuid5(document_id, f"{version}:{page['page_number']}")
            image = page["image"]
            hashes.append(hashlib.sha256(image).hexdigest())
            key = f"{workspace_id}/{document_id}/{version}/{page['page_number']}.png"
            self.objects.put(key, image, "image/png")
            staged.append((page_id, key, page))
        content = [{k: v for k, v in p.items() if k != "image"} for p in parsed]
        output_hash = fingerprint({"pages": content, "images": hashes})
        status = (
            "needs_review"
            if any(p["processing_status"] != "complete" for p in parsed)
            else "complete"
        )
        with self.engine.begin() as c:
            self._document(c, workspace_id, document_id, lock=True)
            self._fence(c, job)
            for page_id, key, p in staged:
                c.execute(
                    t.pages.insert().values(
                        id=page_id,
                        workspace_id=workspace_id,
                        document_id=document_id,
                        ingestion_version=version,
                        render_object_key=key,
                        **{
                            k: p[k]
                            for k in (
                                "page_number",
                                "width",
                                "height",
                                "rotation",
                                "text_raw",
                                "processing_status",
                                "metadata_json",
                            )
                        },
                    )
                )
                for order, element in enumerate(p["elements"]):
                    element = dict(element)
                    parent = element.pop("parent_order", None)
                    c.execute(
                        t.elements.insert().values(
                            id=uuid5(page_id, str(order)),
                            workspace_id=workspace_id,
                            document_id=document_id,
                            ingestion_version=version,
                            page_id=page_id,
                            parent_element_id=uuid5(page_id, str(parent))
                            if parent is not None
                            else None,
                            reading_order=order,
                            text_normalized=" ".join(element["text_raw"].split()),
                            **element,
                        )
                    )
            c.execute(
                t.versions.update()
                .where(
                    t.versions.c.workspace_id == workspace_id,
                    t.versions.c.document_id == document_id,
                    t.versions.c.ingestion_version == version,
                )
                .values(extraction_status=status, output_hash=output_hash)
            )
            c.execute(
                t.documents.update()
                .where(
                    t.documents.c.id == document_id,
                    sa.or_(
                        t.documents.c.latest_ingestion_version == version,
                        t.documents.c.preview_ingestion_version.is_(None),
                    ),
                )
                .values(preview_ingestion_version=version)
            )
            c.execute(
                t.jobs.update()
                .where(t.jobs.c.id == job["id"])
                .values(
                    status="succeeded",
                    finished_at=now(),
                    lease_token=None,
                    lease_expires_at=None,
                    error_code=None,
                )
            )

    def _fence(self, c, job):
        j = (
            c.execute(sa.select(t.jobs).where(t.jobs.c.id == job["id"]).with_for_update())
            .mappings()
            .one()
        )
        if (
            j["status"] != "running"
            or j["lease_token"] != job["lease_token"]
            or j["lease_expires_at"] <= now()
        ):
            raise AppError("LEASE_LOST", 409)

    def fail(self, job, code, *, permanent=False):
        with self.engine.begin() as c:
            c.execute(
                sa.select(t.documents)
                .where(t.documents.c.id == job["document_id"])
                .with_for_update()
            )
            try:
                self._fence(c, job)
            except AppError:
                return
            retry = not permanent and job["attempts"] < self.cfg.job_max_attempts
            c.execute(
                t.jobs.update()
                .where(t.jobs.c.id == job["id"])
                .values(
                    status="queued" if retry else "failed",
                    error_code=code,
                    lease_token=None,
                    lease_expires_at=None,
                    finished_at=None if retry else now(),
                    next_attempt_at=now()
                    + timedelta(
                        seconds=min(
                            self.cfg.job_retry_max_seconds,
                            self.cfg.job_retry_base_seconds * 2 ** (job["attempts"] - 1),
                        )
                    ),
                )
            )
            if job["kind"] == "ingest":
                c.execute(
                    t.versions.update()
                    .where(
                        t.versions.c.workspace_id == job["workspace_id"],
                        t.versions.c.document_id == job["document_id"],
                        t.versions.c.ingestion_version == job["ingestion_version"],
                    )
                    .values(extraction_status="queued" if retry else "failed", error_code=code)
                )

    def due_jobs(self):
        with self.engine.connect() as c:
            expired = (
                c.execute(
                    sa.select(t.jobs.c.id).where(
                        t.jobs.c.status == "running", t.jobs.c.lease_expires_at < now()
                    )
                )
                .scalars()
                .all()
            )
        for job_id in expired:
            with self.engine.begin() as c:
                j = c.execute(sa.select(t.jobs).where(t.jobs.c.id == job_id)).mappings().one()
                c.execute(
                    sa.select(t.documents)
                    .where(t.documents.c.id == j["document_id"])
                    .with_for_update()
                )
                j = (
                    c.execute(sa.select(t.jobs).where(t.jobs.c.id == job_id).with_for_update())
                    .mappings()
                    .one()
                )
                if j["status"] != "running" or j["lease_expires_at"] >= now():
                    continue
                status = "failed" if j["attempts"] >= self.cfg.job_max_attempts else "queued"
                c.execute(
                    t.jobs.update()
                    .where(t.jobs.c.id == job_id)
                    .values(
                        status=status,
                        next_attempt_at=now(),
                        error_code="WORKER_LEASE_EXPIRED",
                        lease_token=None,
                        lease_expires_at=None,
                        finished_at=now() if status == "failed" else None,
                    )
                )
                if j["kind"] == "ingest":
                    c.execute(
                        t.versions.update()
                        .where(
                            t.versions.c.workspace_id == j["workspace_id"],
                            t.versions.c.document_id == j["document_id"],
                            t.versions.c.ingestion_version == j["ingestion_version"],
                        )
                        .values(
                            extraction_status="failed" if status == "failed" else "queued",
                            error_code="WORKER_LEASE_EXPIRED",
                        )
                    )
        with self.engine.connect() as c:
            return (
                c.execute(
                    sa.select(t.jobs.c.id)
                    .where(t.jobs.c.status == "queued", t.jobs.c.next_attempt_at <= now())
                    .order_by(t.jobs.c.created_at)
                    .limit(100)
                )
                .scalars()
                .all()
            )

    def cleanup_document(self, job):
        # Deletion already revoked access. Retain source metadata tombstones for retry/idempotency.
        for obj in self.objects.keys(f"{job['workspace_id']}/{job['document_id']}/"):
            self.heartbeat(job)
            self.objects.delete(obj["Key"])
        with self.engine.begin() as c:
            c.execute(
                sa.select(t.documents)
                .where(t.documents.c.id == job["document_id"])
                .with_for_update()
            )
            self._fence(c, job)
            for table in [t.elements, t.pages]:
                c.execute(
                    table.delete().where(
                        table.c.workspace_id == job["workspace_id"],
                        table.c.document_id == job["document_id"],
                    )
                )
            c.execute(
                t.documents.update()
                .where(t.documents.c.id == job["document_id"])
                .values(
                    lifecycle_status="deleted",
                    preview_ingestion_version=None,
                    filename="deleted.pdf",
                )
            )
            c.execute(
                t.jobs.update()
                .where(t.jobs.c.id == job["id"])
                .values(
                    status="succeeded", finished_at=now(), lease_token=None, lease_expires_at=None
                )
            )

    def collect_orphans(self):
        cutoff = now() - timedelta(seconds=self.cfg.orphan_grace_seconds)
        for obj in self.objects.keys():
            if obj["LastModified"] >= cutoff:
                continue
            key = obj["Key"]
            with self.engine.connect() as c:
                used = c.execute(
                    sa.select(t.documents.c.id).where(
                        t.documents.c.object_key == key, t.documents.c.lifecycle_status != "deleted"
                    )
                ).first()
                used = (
                    used
                    or c.execute(
                        sa.select(t.pages.c.id).where(t.pages.c.render_object_key == key)
                    ).first()
                )
                parts = key.split("/")
                try:
                    doc_id = UUID(parts[1])
                except (ValueError, IndexError):
                    doc_id = None
                in_progress = (
                    c.execute(
                        sa.select(t.jobs.c.id).where(
                            t.jobs.c.document_id == doc_id,
                            t.jobs.c.status.in_(["queued", "running"]),
                        )
                    ).first()
                    if doc_id
                    else None
                )
                if not used and not in_progress:
                    self.objects.delete(key)
