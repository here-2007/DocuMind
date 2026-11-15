"""Build and pin matching dense and lexical corpus snapshots."""

import hashlib
import json
from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa

from documind import schema as t
from documind.db import artifact_lock
from documind.errors import AppError
from documind.parser import fingerprint
from documind.phase_jobs import lock_workspace, queue, record_request, request, terminal
from documind.repository import now
from documind.search_index import (
    DenseIndex,
    chunks_for,
    point_payload,
    rank_passages,
    sparse_tokens,
)
from documind.search_models import search_models

MAX_SPARSE_BYTES = 32 * 1024 * 1024


def publication(c, workspace, identifier):
    value = (
        c.execute(
            sa.select(t.publications).where(
                t.publications.c.workspace_id == workspace,
                t.publications.c.id == identifier,
            )
        )
        .mappings()
        .first()
    )
    if not value:
        raise AppError("NOT_FOUND", 404)
    return value


def active_id(c, workspace):
    return c.scalar(
        sa.select(t.workspaces.c.active_publication_id).where(t.workspaces.c.id == workspace)
    )


def members(c, workspace, identifier):
    return {
        row.document_id: row.ingestion_version
        for row in c.execute(
            sa.select(
                t.publication_documents.c.document_id, t.publication_documents.c.ingestion_version
            ).where(
                t.publication_documents.c.workspace_id == workspace,
                t.publication_documents.c.publication_id == identifier,
            )
        )
    }


def eligible_versions(c, workspace, parent):
    previous = members(c, workspace, parent) if parent else {}
    rows = c.execute(
        sa.select(
            t.documents.c.id,
            t.documents.c.preview_ingestion_version,
            t.documents.c.last_published_ingestion_version,
            t.versions.c.extraction_status,
        )
        .outerjoin(
            t.versions,
            sa.and_(
                t.versions.c.workspace_id == t.documents.c.workspace_id,
                t.versions.c.document_id == t.documents.c.id,
                t.versions.c.ingestion_version == t.documents.c.preview_ingestion_version,
            ),
        )
        .where(
            t.documents.c.workspace_id == workspace,
            t.documents.c.lifecycle_status == "active",
        )
    )
    selected = {}
    for row in rows:
        if row.extraction_status == "complete":
            selected[row.id] = row.preview_ingestion_version
        elif row.id in previous:
            selected[row.id] = previous[row.id]
        elif parent is None and row.last_published_ingestion_version:
            selected[row.id] = row.last_published_ingestion_version
    return selected


def enqueue_publication(c, repo, workspace):
    parent = active_id(c, workspace)
    versions = eligible_versions(c, workspace, parent)
    identifier = queue(c, workspace, "publication")
    c.execute(
        t.publications.insert().values(
            workspace_id=workspace,
            id=identifier,
            parent_id=parent,
            collection=repo.cfg.qdrant_collection,
        )
    )
    if versions:
        c.execute(
            t.publication_documents.insert(),
            [
                {
                    "workspace_id": workspace,
                    "publication_id": identifier,
                    "document_id": document,
                    "ingestion_version": version,
                }
                for document, version in versions.items()
            ],
        )
    return identifier


def start_publication(repo, workspace, key):
    repo.authorize(workspace)
    if not repo.cfg.indexing_enabled:
        raise AppError("SEARCH_DISABLED", 409)
    digest = fingerprint({})
    with repo.engine.begin() as c:
        previous = request(c, workspace, "publication", key, digest)
        if previous:
            return previous["resource_id"]
        identifier = enqueue_publication(c, repo, workspace)
        record_request(c, workspace, "publication", key, digest, identifier, identifier)
        return identifier


def sparse_key(workspace, identifier):
    return f"index/{workspace}/{identifier}.json"


def index_lock(repo, identifier):
    return artifact_lock(repo.engine, identifier)


def source_elements(c, workspace, identifier, document_ids=None):
    statement = (
        sa.select(t.elements, t.pages.c.page_number)
        .join(
            t.publication_documents,
            sa.and_(
                t.publication_documents.c.workspace_id == t.elements.c.workspace_id,
                t.publication_documents.c.document_id == t.elements.c.document_id,
                t.publication_documents.c.ingestion_version == t.elements.c.ingestion_version,
            ),
        )
        .join(t.pages, t.pages.c.id == t.elements.c.page_id)
        .where(
            t.elements.c.workspace_id == workspace,
            t.publication_documents.c.publication_id == identifier,
        )
    )
    if document_ids is not None:
        statement = statement.where(t.elements.c.document_id.in_(document_ids))
    return (
        c.execute(
            statement.order_by(
                t.elements.c.document_id,
                t.pages.c.page_number,
                t.elements.c.reading_order,
            )
        )
        .mappings()
        .all()
    )


def table_regions(element, cells):
    rows = {}
    for cell in cells:
        coordinates = cell["table_coordinates"]
        rows.setdefault(coordinates["row_index"], []).append(cell)
    headers = element["metadata_json"].get("headers") or []
    prefix = " | ".join(label for label in headers if isinstance(label, str) and label.strip())
    for row, entries in sorted(rows.items()):
        start = min(cell["start_char"] for cell in entries) - element["start_char"]
        end = max(cell["end_char"] for cell in entries) - element["start_char"]
        if end > start:
            yield (
                start,
                end,
                (prefix + "\n" if prefix else ""),
                {
                    "table_id": str(element["id"]),
                    "row_index": row,
                    "columns": [cell["table_coordinates"] for cell in entries],
                    "headers": headers,
                },
            )


def build_chunks(repo, job, elements, models):
    children = {}
    for element in elements:
        if element["element_type"] == "table_cell":
            children.setdefault(element["parent_element_id"], []).append(element)
    chunks = []
    for element in elements:
        if element["element_type"] == "table_cell":
            continue
        regions = None
        if element["element_type"] == "table":
            regions = table_regions(element, children.get(element["id"], ()))
        for chunk in chunks_for(job["id"], element, models.tokenizer, regions=regions):
            chunks.append(chunk)
            if len(chunks) > repo.cfg.max_publication_chunks:
                raise AppError("PUBLICATION_CHUNK_LIMIT", 422)
            if len(chunks) % 32 == 0:
                repo.heartbeat(job)
    return chunks


def dense_for(repo, item):
    # Retired publications retain their original collection identity.
    return DenseIndex(repo.cfg.model_copy(update={"qdrant_collection": item["collection"]}))


def build_publication(repo, job):
    if not repo.cfg.indexing_enabled:
        raise AppError("SEARCH_DISABLED", 409)
    workspace, identifier = job["workspace_id"], job["id"]
    with index_lock(repo, workspace), index_lock(repo, identifier):
        with repo.engine.begin() as c:
            lock_workspace(c, workspace)
            repo._fence(c, job)
            item = publication(c, workspace, identifier)
            frozen = members(c, workspace, identifier)
            check_build(c, item, frozen)
            elements = source_elements(c, workspace, identifier)
        models = search_models()
        chunks = build_chunks(repo, job, elements, models)
        chunks.sort(key=lambda chunk: str(chunk["id"]))
        sparse = [
            {"id": str(chunk["id"]), "tokens": sparse_tokens(chunk["text"])} for chunk in chunks
        ]
        if any(not item["tokens"] for item in sparse):
            raise AppError("INVALID_SPARSE_CHUNK", 422)
        artifact = json.dumps(sparse, ensure_ascii=False, separators=(",", ":")).encode()
        if len(artifact) > MAX_SPARSE_BYTES:
            raise AppError("PUBLICATION_CHUNK_LIMIT", 422)
        key = sparse_key(workspace, identifier)
        dense = dense_for(repo, item)
        dense.ensure()
        # A restarted attempt can safely replace its unpublished staging artifacts.
        dense.remove(workspace, identifier)
        repo.objects.delete(key)
        try:
            for offset in range(0, len(chunks), 16):
                batch = chunks[offset : offset + 16]
                repo.heartbeat(job)
                dense.write(batch, models.embed([chunk["text"] for chunk in batch]))
            repo.objects.put(key, artifact, "application/json")
            expected = {str(chunk["id"]): point_payload(chunk) for chunk in chunks}
            if dense.members(workspace, identifier) != expected:
                raise AppError("INDEX_MEMBERSHIP_MISMATCH", 503)
            manifest = {
                "encoding": models.identity,
                "chunk_count": len(chunks),
                "membership_hash": fingerprint(expected),
                "sparse_hash": hashlib.sha256(artifact).hexdigest(),
            }
            with repo.engine.begin() as c:
                lock_workspace(c, workspace)
                repo._fence(c, job)
                check_build(c, publication(c, workspace, identifier), frozen)
                if chunks:
                    c.execute(t.chunks.insert(), chunks)
                if item["parent_id"]:
                    c.execute(
                        t.publications.update()
                        .where(
                            t.publications.c.workspace_id == workspace,
                            t.publications.c.id == item["parent_id"],
                        )
                        .values(status="retired")
                    )
                c.execute(
                    t.publications.update()
                    .where(
                        t.publications.c.workspace_id == workspace,
                        t.publications.c.id == identifier,
                    )
                    .values(status="published", manifest=manifest, sparse_key=key)
                )
                c.execute(
                    t.workspaces.update()
                    .where(t.workspaces.c.id == workspace)
                    .values(active_publication_id=identifier)
                )
                c.execute(
                    t.documents.update()
                    .where(
                        t.documents.c.workspace_id == workspace,
                        t.publication_documents.c.workspace_id == workspace,
                        t.publication_documents.c.publication_id == identifier,
                        t.publication_documents.c.document_id == t.documents.c.id,
                    )
                    .values(
                        last_published_ingestion_version=t.publication_documents.c.ingestion_version
                    )
                )
                terminal(c, job, "succeeded")
        except BaseException:
            # A connection loss after commit has an ambiguous outcome. Re-read before cleanup.
            with repo.engine.connect() as c:
                committed = publication(c, workspace, identifier)["manifest"] is not None
            if not committed:
                dense.remove(workspace, identifier)
                repo.objects.delete(key)
            raise


def check_build(c, item, frozen):
    workspace = item["workspace_id"]
    parent = active_id(c, workspace)
    if item["revoked_at"] is not None:
        raise AppError("SOURCE_UNAVAILABLE", 410)
    if parent != item["parent_id"] or eligible_versions(c, workspace, parent) != frozen:
        raise AppError("PUBLICATION_STALE", 409)


def assert_visible(c, workspace, versions):
    if not versions:
        return
    actual = set(
        c.scalars(
            sa.select(t.documents.c.id).where(
                t.documents.c.workspace_id == workspace,
                t.documents.c.id.in_(versions),
                t.documents.c.lifecycle_status == "active",
            )
        )
    )
    if actual != set(versions):
        raise AppError("SOURCE_UNAVAILABLE", 410)


def checked_artifacts(repo, item, identity):
    workspace_id, publication_id = item["workspace_id"], item["id"]
    if item["revoked_at"] or item["status"] not in {"published", "retired"}:
        raise AppError("INDEX_UNAVAILABLE", 503)
    manifest = item["manifest"]
    if identity != manifest["encoding"]:
        raise AppError("INDEX_IDENTITY_MISMATCH", 503)
    with repo.engine.connect() as c:
        chunks = (
            c.execute(
                sa.select(t.chunks, t.pages.c.page_number)
                .join(t.pages, t.pages.c.id == t.chunks.c.page_id)
                .where(
                    t.chunks.c.workspace_id == workspace_id,
                    t.chunks.c.publication_id == publication_id,
                )
                .order_by(t.chunks.c.id)
            )
            .mappings()
            .all()
        )
    try:
        artifact = repo.objects.get(item["sparse_key"], MAX_SPARSE_BYTES)
        sparse = json.loads(artifact)
    except (OSError, ValueError) as exc:
        raise AppError("SPARSE_INDEX_UNAVAILABLE", 503) from exc
    expected = {str(chunk["id"]): point_payload(chunk) for chunk in chunks}
    dense = dense_for(repo, item)
    if (
        hashlib.sha256(artifact).hexdigest() != manifest["sparse_hash"]
        or fingerprint(expected) != manifest["membership_hash"]
        or len(chunks) != manifest["chunk_count"]
        or set(expected) != {entry["id"] for entry in sparse}
        or dense.members(workspace_id, publication_id) != expected
    ):
        raise AppError("INDEX_MEMBERSHIP_MISMATCH", 503)
    return chunks, sparse, dense


def retrieve(repo, query, *, workspace_id, publication_id, document_ids=None):
    repo.authorize(workspace_id)
    if not query.strip():
        raise AppError("INVALID_QUESTION", 422)
    if not repo.cfg.indexing_enabled:
        raise AppError("SEARCH_DISABLED", 409)
    with repo.engine.connect() as c:
        item = publication(c, workspace_id, publication_id)
        frozen = members(c, workspace_id, publication_id)
        effective = set(frozen if document_ids is None else document_ids)
        if not effective or not effective.issubset(frozen):
            raise AppError("INVALID_DOCUMENT_SCOPE", 422)
        assert_visible(c, workspace_id, effective)
    models = search_models()
    models.validate_query(query)
    chunks, sparse, dense = checked_artifacts(repo, item, models.identity)
    eligible = [chunk for chunk in chunks if chunk["document_id"] in effective]
    hits = (
        dense.search(
            workspace_id,
            publication_id,
            sorted(effective),
            models.embed([query], query=True)[0],
        )
        if eligible
        else []
    )
    results = rank_passages(query, eligible, sparse, hits, models)
    with repo.engine.connect() as c:
        assert_visible(c, workspace_id, effective)
        if publication(c, workspace_id, publication_id)["revoked_at"] is not None:
            raise AppError("SOURCE_UNAVAILABLE", 410)
    return results


def revoke_source(c, workspace, document):
    affected = sa.select(t.publication_documents.c.publication_id).where(
        t.publication_documents.c.workspace_id == workspace,
        t.publication_documents.c.document_id == document,
    )
    c.execute(
        t.publications.update()
        .where(
            t.publications.c.workspace_id == workspace,
            t.publications.c.id.in_(affected),
            t.publications.c.revoked_at.is_(None),
        )
        .values(revoked_at=now(), status="retired")
    )
    c.execute(
        t.workspaces.update()
        .where(
            t.workspaces.c.id == workspace,
            t.workspaces.c.active_publication_id.in_(affected),
        )
        .values(active_publication_id=None)
    )
    c.execute(
        t.query_runs.update()
        .where(
            t.query_runs.c.workspace_id == workspace,
            t.query_runs.c.publication_id.in_(affected),
            t.query_runs.c.revoked_at.is_(None),
        )
        .values(revoked_at=now(), question=None, result=None, runtime_progress=None)
    )
    runs = sa.select(t.query_runs.c.id).where(
        t.query_runs.c.workspace_id == workspace,
        t.query_runs.c.publication_id.in_(affected),
    )
    c.execute(
        t.citations.delete().where(
            t.citations.c.workspace_id == workspace,
            t.citations.c.query_id.in_(runs),
        )
    )
    c.execute(
        t.query_events.delete().where(
            t.query_events.c.workspace_id == workspace,
            t.query_events.c.query_id.in_(runs),
        )
    )


def purge_indexes(repo, job):
    workspace, document = job["workspace_id"], job["document_id"]
    with repo.engine.connect() as c:
        ids = c.scalars(
            sa.select(t.publication_documents.c.publication_id).where(
                t.publication_documents.c.workspace_id == workspace,
                t.publication_documents.c.document_id == document,
            )
        ).all()
    for identifier in ids:
        with index_lock(repo, identifier):
            repo.heartbeat(job)
            with repo.engine.connect() as c:
                item = publication(c, workspace, identifier)
            dense_for(repo, item).remove(workspace, identifier)
            repo.objects.delete(sparse_key(workspace, identifier))
            with repo.engine.begin() as c:
                lock_workspace(c, workspace)
                repo._fence(c, job)
                purge_members(c, workspace, identifier)
    return bool(ids)


def purge_members(c, workspace, identifier):
    for table in (t.query_documents, t.chunks, t.publication_documents):
        c.execute(
            table.delete().where(
                table.c.workspace_id == workspace,
                table.c.publication_id == identifier,
            )
        )


def artifact_referenced(c, key):
    parts = key.split("/")
    if len(parts) != 3 or parts[0] != "index" or not parts[2].endswith(".json"):
        return False
    try:
        workspace, identifier = UUID(parts[1]), UUID(parts[2][:-5])
    except ValueError:
        return False
    return bool(
        c.scalar(
            sa.select(t.publications.c.id).where(
                t.publications.c.workspace_id == workspace,
                t.publications.c.id == identifier,
                t.publications.c.revoked_at.is_(None),
                t.publications.c.status != "failed",
            )
        )
    )


def collect_failed_indexes(repo):
    cutoff = now() - timedelta(seconds=repo.cfg.orphan_grace_seconds)
    with repo.engine.connect() as c:
        stale = (
            c.execute(
                sa.select(t.publications)
                .join(
                    t.jobs,
                    t.jobs.c.id == t.publications.c.id,
                )
                .where(
                    t.publications.c.status == "failed",
                    t.jobs.c.finished_at < cutoff,
                    sa.exists(
                        sa.select(t.publication_documents.c.document_id).where(
                            t.publication_documents.c.workspace_id == t.publications.c.workspace_id,
                            t.publication_documents.c.publication_id == t.publications.c.id,
                        )
                    ),
                )
                .limit(10)
            )
            .mappings()
            .all()
        )
    for item in stale:
        workspace, identifier = item["workspace_id"], item["id"]
        try:
            with index_lock(repo, identifier):
                dense_for(repo, item).remove(workspace, identifier)
                repo.objects.delete(sparse_key(workspace, identifier))
                with repo.engine.begin() as c:
                    lock_workspace(c, workspace)
                    purge_members(c, workspace, identifier)
        except AppError as exc:
            if exc.code != "PUBLICATION_BUSY":
                raise
