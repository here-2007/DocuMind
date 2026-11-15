import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from documind import schema as t
from documind.errors import AppError
from documind.parser import extract_pdf
from documind.repository import now
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def test_http_upload_preview_and_scope(client, repo, pdf):
    base = f"/api/v1/workspaces/{repo.cfg.local_workspace_id}"
    assert client.get("/api/v1/health").json()["status"] == "alive"
    assert client.get("/api/v1/ready").json()["generation"] is False
    response = client.post(
        base + "/documents",
        files={"file": ("invoice.pdf", pdf())},
        headers={"Idempotency-Key": "upload-1"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert not body["preview_available"] and not body["searchable"]
    from documind.worker import process_job

    process_job(UUID(body["job_id"]))
    path = base + "/documents/" + body["document_id"]
    result = client.get(path).json()
    assert result["extraction_status"] == "complete" and result["preview_available"]
    assert result["index_status"] == "not_started" and not result["searchable"]
    page = client.get(path + "/pages/1").json()
    assert "Grand total" in page["text_raw"]
    assert client.get(page["image_url"]).content.startswith(b"\x89PNG")
    assert client.get(path + "/original").content.startswith(b"%PDF-")
    element = client.get(path + "/elements").json()["items"][0]
    assert page["text_raw"][element["start_char"] : element["end_char"]] == element["text_raw"]
    assert element["confidence"] is None
    assert client.get(path + "/pages/0").status_code == 422
    assert client.get(path + "/pages/1?ingestion_version=wrong").status_code == 404
    assert (
        client.get(path.replace(str(repo.cfg.local_workspace_id), str(uuid4()))).status_code == 404
    )
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert "X-Request-ID" in response.headers


def test_idempotency_concurrency_conflict_and_invalid_input(client, repo, pdf):
    workspace = repo.cfg.local_workspace_id
    data = pdf()
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: repo.upload(workspace, data, "a.pdf", "same"), range(8)))
    assert len({r["document_id"] for r in rows}) == 1
    with pytest.raises(AppError, match="IDEMPOTENCY_CONFLICT"):
        repo.upload(workspace, pdf("different"), "a.pdf", "same")
    with repo.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(t.documents)).scalar_one() == 1
        assert c.execute(sa.select(sa.func.count()).select_from(t.jobs)).scalar_one() == 1
    path = f"/api/v1/workspaces/{workspace}/documents"
    assert (
        client.post(
            path, files={"file": ("a.pdf", b"bad")}, headers={"Idempotency-Key": "bad"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            path, files={"file": ("a.exe", data)}, headers={"Idempotency-Key": "exe"}
        ).status_code
        == 400
    )
    assert client.post(path, files={"file": ("a.pdf", data)}).status_code == 422
    assert client.get(path + "?cursor=invalid").status_code == 400


def test_duplicate_worker_and_expired_lease_fencing(repo, pdf):
    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "a")
    first = repo.claim(row["job_id"])
    assert repo.claim(row["job_id"]) is None
    with repo.engine.begin() as c:
        c.execute(
            t.jobs.update()
            .where(t.jobs.c.id == row["job_id"])
            .values(lease_expires_at=now() - timedelta(seconds=1))
        )
    assert row["job_id"] in repo.due_jobs()
    second = repo.claim(row["job_id"])
    parsed = extract_pdf(pdf(), repo.parser_config(), 10)
    with pytest.raises(AppError, match="LEASE_LOST"):
        repo.promote(first, parsed)
    repo.promote(second, parsed)
    assert repo.claim(row["job_id"]) is None
    with repo.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(t.pages)).scalar_one() == 1


def test_failure_retry_and_needs_review(repo, pdf):
    from documind.worker import process_job

    row = repo.upload(repo.cfg.local_workspace_id, b"%PDF-broken", "a.pdf", "broken")
    process_job(row["job_id"])
    assert (
        repo.summary(repo.cfg.local_workspace_id, row["document_id"])["extraction_status"]
        == "failed"
    )
    repo.retry(repo.cfg.local_workspace_id, row["job_id"], "retry-key")
    process_job(row["job_id"])
    assert repo.job(repo.cfg.local_workspace_id, row["job_id"])["status"] == "failed"
    scan = repo.upload(repo.cfg.local_workspace_id, pdf(blank=True), "scan.pdf", "scan")
    process_job(scan["job_id"])
    status = repo.summary(repo.cfg.local_workspace_id, scan["document_id"])
    assert status["extraction_status"] == "needs_review" and status["preview_available"]
    assert not status["searchable"]


def test_transient_attempt_budget_and_retry(repo, pdf):
    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "retry")
    job = repo.claim(row["job_id"])
    repo.fail(job, "DEPENDENCY_UNAVAILABLE")
    assert repo.job(repo.cfg.local_workspace_id, row["job_id"])["status"] == "queued"
    with repo.engine.begin() as c:
        c.execute(
            t.jobs.update()
            .where(t.jobs.c.id == row["job_id"])
            .values(attempts=repo.cfg.job_max_attempts - 1, next_attempt_at=now())
        )
    job = repo.claim(row["job_id"])
    repo.fail(job, "DEPENDENCY_UNAVAILABLE")
    assert repo.job(repo.cfg.local_workspace_id, row["job_id"])["status"] == "failed"
    repo.retry(repo.cfg.local_workspace_id, row["job_id"], "retry-key")
    assert repo.job(repo.cfg.local_workspace_id, row["job_id"])["attempts"] == 0


def test_deletion_wins_and_cleanup_is_idempotent(repo, pdf):
    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "delete")
    active = repo.claim(row["job_id"])
    delete_id = repo.delete(repo.cfg.local_workspace_id, row["document_id"])
    assert delete_id == repo.delete(repo.cfg.local_workspace_id, row["document_id"])
    with pytest.raises(AppError, match="LEASE_LOST"):
        repo.promote(active, extract_pdf(pdf(), repo.parser_config(), 10))
    from documind.worker import process_job

    process_job(delete_id)
    process_job(delete_id)
    assert repo.job(repo.cfg.local_workspace_id, delete_id)["status"] == "succeeded"
    with pytest.raises(AppError, match="SOURCE_UNAVAILABLE"):
        repo.source(repo.cfg.local_workspace_id, row["document_id"])


def test_cleanup_cannot_finish_between_heartbeat_and_page_write(repo, pdf, monkeypatch):
    from threading import Event

    from documind.worker import process_job

    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "race.pdf", "race")
    job = repo.claim(row["job_id"])
    parsed = extract_pdf(pdf(), repo.parser_config(), 10)
    entered, release = Event(), Event()
    put = repo.objects.put

    def pause(key, data, media):
        if key.endswith(".png"):
            entered.set()
            assert release.wait(5)
        return put(key, data, media)

    monkeypatch.setattr(repo.objects, "put", pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        promotion = executor.submit(repo.promote, job, parsed)
        try:
            assert entered.wait(5)
            cleanup = repo.delete(repo.cfg.local_workspace_id, row["document_id"])
            started = time.monotonic()
            process_job(cleanup)
            assert time.monotonic() - started < repo.cfg.job_lease_seconds
            status = repo.job(repo.cfg.local_workspace_id, cleanup)
            assert status["status"] == "queued" and status["error_code"] == "PUBLICATION_BUSY"
        finally:
            release.set()
        with pytest.raises(AppError, match="SOURCE_UNAVAILABLE|LEASE_LOST"):
            promotion.result(timeout=5)
    with repo.engine.begin() as c:
        c.execute(t.jobs.update().where(t.jobs.c.id == cleanup).values(next_attempt_at=now()))
    process_job(cleanup)
    assert repo.job(repo.cfg.local_workspace_id, cleanup)["status"] == "succeeded"
    prefix = f"{repo.cfg.local_workspace_id}/{row['document_id']}/"
    assert list(repo.objects.keys(prefix)) == []
    with pytest.raises(AppError, match="LEASE_LOST"):
        repo.promote(job, parsed)
    assert list(repo.objects.keys(prefix)) == []


def test_stale_schema_rejects_readiness_and_operations(client, repo, pdf):
    config = Config("alembic.ini")
    command.downgrade(config, "0009_split_reflection")
    try:
        ready = client.get("/api/v1/ready")
        assert ready.status_code == 503
        assert ready.json()["database_schema_current"] is False
        assert ready.json()["error"]["code"] == "DATABASE_MIGRATION_REQUIRED"
        response = client.post(
            f"/api/v1/workspaces/{repo.cfg.local_workspace_id}/documents",
            headers={"Idempotency-Key": "stale"},
            files={"file": ("a.pdf", pdf())},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_MIGRATION_REQUIRED"
    finally:
        command.upgrade(config, "head")
    assert client.get("/api/v1/ready").status_code == 200


def test_source_immutability_and_composite_scope(repo, pdf):
    from documind.worker import process_job

    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "immutable")
    process_job(row["job_id"])
    for statement in [
        t.pages.update().values(text_raw="tampered"),
        t.elements.update().values(text_raw="tampered"),
        t.pages.delete(),
    ]:
        with pytest.raises(DBAPIError), repo.engine.begin() as c:
            c.execute(statement)
    other = repo.upload(repo.cfg.local_workspace_id, pdf(), "b.pdf", "other")
    with repo.engine.begin() as c:
        c.execute(
            t.versions.update()
            .where(t.versions.c.document_id == other["document_id"])
            .values(extraction_status="processing")
        )
        p = dict(c.execute(t.pages.select()).mappings().one())
    # Every referenced UUID exists, but page belongs to the other document/version.
    with pytest.raises(DBAPIError), repo.engine.begin() as c:
        c.execute(
            t.elements.insert().values(
                id=uuid4(),
                workspace_id=repo.cfg.local_workspace_id,
                document_id=other["document_id"],
                ingestion_version=other["latest_ingestion_version"],
                page_id=p["id"],
                element_type="paragraph",
                text_raw="x",
                text_normalized="x",
                bbox_norm=[0, 0, 1, 1],
                reading_order=0,
                source_type="native_text",
                start_char=0,
                end_char=1,
            )
        )


def test_migration_roundtrip(repo):
    # This repo fixture belongs to a randomly named disposable test database.
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
    repo.initialize()
    with repo.engine.connect() as c:
        assert (
            c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0010_artifacts_dispatch"
        )


def test_real_celery_dispatch_and_worker(repo, pdf, tmp_path):
    from documind.worker import dispatch_once

    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "queue.pdf", "queue")
    log = tmp_path / "worker.log"
    with log.open("w") as out:
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "documind.worker:celery",
                "worker",
                "--pool=solo",
                "--loglevel=WARNING",
            ],
            env=os.environ.copy(),
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        try:
            assert dispatch_once() >= 1
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status = repo.job(repo.cfg.local_workspace_id, row["job_id"])["status"]
                if status in {"succeeded", "failed"}:
                    break
                time.sleep(0.2)
            assert status == "succeeded", log.read_text()[-2000:]
        finally:
            worker.terminate()
            worker.wait(timeout=15)


def test_broker_outage_keeps_durable_work(repo, pdf, monkeypatch):
    from documind.worker import dispatch_once, ingest

    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "queue.pdf", "outage")

    def unavailable(*a, **kw):
        raise ConnectionError("simulated broker outage")

    monkeypatch.setattr(ingest, "apply_async", unavailable)
    assert dispatch_once() == 0
    # An ambiguous send is retried after the reservation.
    assert row["job_id"] not in repo.due_jobs()
    assert repo.job(repo.cfg.local_workspace_id, row["job_id"])["status"] == "queued"
    with repo.engine.begin() as c:
        c.execute(t.jobs.update().where(t.jobs.c.id == row["job_id"]).values(dispatch_after=now()))
    assert row["job_id"] in repo.due_jobs()


def test_concurrent_dispatchers_reserve_once_and_resend_after_expiry(repo, pdf, monkeypatch):
    from documind.worker import dispatch_once, ingest

    waiting = [
        str(repo.upload(repo.cfg.local_workspace_id, pdf(), "queue.pdf", str(i))["job_id"])
        for i in range(3)
    ]
    sends = []
    monkeypatch.setattr(ingest, "apply_async", lambda *, args: sends.extend(args))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _: dispatch_once(), range(10))) == 3
    assert sorted(sends) == sorted(waiting)
    with repo.engine.begin() as c:
        c.execute(t.jobs.update().values(dispatch_after=now()))
    assert dispatch_once() == 3
    assert sorted(sends) == sorted(waiting * 2)


def test_readiness_requires_the_configured_workspace(client, repo, pdf):
    workspace = repo.cfg.local_workspace_id
    with repo.engine.begin() as c:
        c.execute(t.workspaces.delete().where(t.workspaces.c.id == workspace))
        c.execute(t.workspaces.insert().values(id=uuid4(), name="Another workspace"))
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 503
    assert not ready.json()["workspace_initialized"] and not ready.json()["native_ingestion"]
    for capability in ("source_reads", "tables", "review", "search", "generation", "self_rag"):
        assert not ready.json()[capability]
    assert client.get("/api/v1/workspaces").json()["error"]["code"] == "WORKSPACE_NOT_INITIALIZED"
    denied = client.post(
        f"/api/v1/workspaces/{workspace}/documents",
        files={"file": ("a.pdf", pdf())},
        headers={"Idempotency-Key": "no-workspace"},
    )
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "WORKSPACE_NOT_INITIALIZED"
    repo.initialize()
    assert client.get("/api/v1/ready").json()["workspace_initialized"]


def test_reprocessing_failure_preserves_previous_preview(repo, pdf):
    from documind.worker import process_job

    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "first")
    process_job(row["job_id"])
    old_version = repo.summary(repo.cfg.local_workspace_id, row["document_id"])[
        "preview_ingestion_version"
    ]
    repo.cfg.max_extracted_chars = 1
    try:
        updated = repo.reprocess(repo.cfg.local_workspace_id, row["document_id"], "new-version")
        process_job(updated["job_id"])
        status = repo.summary(repo.cfg.local_workspace_id, row["document_id"])
        assert status["extraction_status"] == "failed"
        assert status["preview_ingestion_version"] == old_version
        assert status["latest_ingestion_version"] != old_version
        assert (
            "Grand total"
            in repo.source(repo.cfg.local_workspace_id, row["document_id"], old_version)[0][
                "text_raw"
            ]
        )
        assert (
            repo.reprocess(repo.cfg.local_workspace_id, row["document_id"], "new-version")["job_id"]
            == updated["job_id"]
        )
    finally:
        repo.cfg.max_extracted_chars = 2000000


def test_request_size_limit(client, repo, pdf):
    previous = repo.cfg.max_upload_bytes
    repo.cfg.max_upload_bytes = 100
    try:
        response = client.post(
            f"/api/v1/workspaces/{repo.cfg.local_workspace_id}/documents",
            files={"file": ("large.pdf", pdf())},
            headers={"Idempotency-Key": "large"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    finally:
        repo.cfg.max_upload_bytes = previous


@pytest.mark.parametrize("legacy", [False, True])
def test_accepted_artifact_and_replay_survive_lower_upload_limit(
    client, repo, pdf, monkeypatch, legacy
):
    import random

    import pymupdf
    from documind.worker import process_job

    with pymupdf.open(stream=pdf("Accepted document bytes")) as source:
        source.embfile_add("fixture.bin", random.Random(10).randbytes(80000))
        data = source.tobytes()
    assert len(data) > 65536
    path = f"/api/v1/workspaces/{repo.cfg.local_workspace_id}/documents"
    request = {"file": ("a.pdf", data)}
    receipt = client.post(path, files=request, headers={"Idempotency-Key": "accepted"})
    assert receipt.status_code == 202
    document = receipt.json()
    if legacy:
        with repo.engine.begin() as c:
            c.execute(
                t.documents.update()
                .where(t.documents.c.id == UUID(document["document_id"]))
                .values(original_size_bytes=None)
            )
    monkeypatch.setattr(repo.cfg, "max_upload_bytes", 100)
    replay = client.post(path, files=request, headers={"Idempotency-Key": "accepted"})
    assert replay.status_code == 202 and replay.json()["job_id"] == document["job_id"]
    assert client.post(path, files=request, headers={"Idempotency-Key": "new"}).status_code == 413
    assert (
        client.post(
            path, files={"file": ("b.pdf", data)}, headers={"Idempotency-Key": "accepted"}
        ).status_code
        == 409
    )
    process_job(UUID(document["job_id"]))
    resource = path + "/" + document["document_id"]
    assert client.get(resource).json()["extraction_status"] == "complete"
    assert client.get(resource + "/original").content == data
    key = f"{repo.cfg.local_workspace_id}/{document['document_id']}/original.pdf"
    repo.objects.path(key).write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    result = client.get(resource + "/original")
    assert result.status_code == 503 and result.json()["error"]["code"] == "SOURCE_HASH_MISMATCH"


def test_unavailable_or_corrupt_preview_has_typed_error(client, repo, pdf):
    from documind.worker import process_job

    workspace = repo.cfg.local_workspace_id
    doc = repo.upload(workspace, pdf(), "a.pdf", "preview")
    process_job(doc["job_id"])
    resource = f"/api/v1/workspaces/{workspace}/documents/{doc['document_id']}"
    page = resource + "/pages/1"
    url = client.get(page).json()["image_url"]
    stored = repo.source(workspace, doc["document_id"], page_number=1)[0]
    key = stored["render_object_key"]
    data = repo.objects.path(key).read_bytes()
    legacy = {**stored, "render_size_bytes": None, "render_sha256": None}
    assert repo.objects.read_preview(legacy) == data
    repo.objects.path(key).write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(AppError, match="SOURCE_HASH_MISMATCH"):
        repo.objects.read_preview(legacy)
    changed = client.get(url)
    assert changed.status_code == 503 and changed.json()["error"]["code"] == "SOURCE_HASH_MISMATCH"
    repo.objects.delete(key)
    assert client.get(resource).json()["preview_available"] is False
    for path in (page, url):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SOURCE_ARTIFACT_UNAVAILABLE"


@pytest.mark.parametrize("attempts", [1, 5])
def test_delete_retries_transient_index_failure_and_exposes_exhaustion(
    client, repo, pdf, monkeypatch, attempts
):
    from documind import publications
    from documind.worker import process_job

    workspace = repo.cfg.local_workspace_id
    base = f"/api/v1/workspaces/{workspace}"
    monkeypatch.setattr(repo.cfg, "max_workspace_documents", 1)
    monkeypatch.setattr(repo.cfg, "job_max_attempts", attempts)
    doc = repo.upload(workspace, pdf(), "a.pdf", "cleanup")
    resource = base + "/documents/" + str(doc["document_id"])
    cleanup_id = UUID(client.delete(resource).json()["job_id"])

    def disconnected(*_):
        raise AppError("DENSE_INDEX_UNAVAILABLE", 503)

    with monkeypatch.context() as fault:
        fault.setattr(publications, "purge_indexes", disconnected)
        process_job(cleanup_id)
    assert client.get(resource).status_code == 410
    status = "failed" if attempts == 1 else "queued"
    assert client.get(base + "/cleanup-jobs").json()["items"] == [
        {"id": str(cleanup_id), "status": status, "error_code": "DENSE_INDEX_UNAVAILABLE"}
    ]
    with pytest.raises(AppError, match="WORKSPACE_DOCUMENT_LIMIT"):
        repo.upload(workspace, pdf(), "b.pdf", "at-capacity")
    if attempts == 1:
        assert (
            client.post(
                base + f"/jobs/{cleanup_id}/retry", headers={"Idempotency-Key": "cleanup-retry"}
            ).status_code
            == 202
        )
    else:
        with repo.engine.begin() as c:
            c.execute(
                t.jobs.update().where(t.jobs.c.id == cleanup_id).values(next_attempt_at=now())
            )
    process_job(cleanup_id)
    assert repo.job(workspace, cleanup_id)["status"] == "succeeded"
    assert client.get(base + "/cleanup-jobs").json()["items"] == []
    assert (
        repo.upload(workspace, pdf(), "b.pdf", "after-cleanup")["document_id"] != doc["document_id"]
    )


def test_workspace_quota_admits_one_concurrent_upload_and_releases_after_cleanup(
    client, repo, pdf, monkeypatch
):
    from documind.worker import process_job

    monkeypatch.setattr(repo.cfg, "max_workspace_documents", 1)
    workspace = repo.cfg.local_workspace_id
    path = f"/api/v1/workspaces/{workspace}/documents"
    data = pdf()

    def object_keys():
        return {item["Key"] for item in repo.objects.keys(f"{workspace}/")}

    previous_objects = object_keys()

    def submit(key):
        return client.post(path, files={"file": ("a.pdf", data)}, headers={"Idempotency-Key": key})

    keys = ["first", "second"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, keys))
    assert sorted(response.status_code for response in responses) == [202, 409]
    index = next(i for i, response in enumerate(responses) if response.status_code == 202)
    accepted, blocked = keys[index], keys[1 - index]
    receipt = responses[index].json()
    assert responses[1 - index].json()["error"]["code"] == "WORKSPACE_DOCUMENT_LIMIT"
    assert submit(accepted).json()["job_id"] == receipt["job_id"]
    assert object_keys() - previous_objects == {
        f"{workspace}/{receipt['document_id']}/original.pdf"
    }
    with repo.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(t.jobs)) == 1
    deleted = client.delete(path + "/" + receipt["document_id"])
    assert deleted.status_code == 202
    assert submit(blocked).status_code == 409
    process_job(UUID(deleted.json()["job_id"]))
    replacement = submit(blocked)
    assert replacement.status_code == 202
    assert replacement.json()["document_id"] != receipt["document_id"]
    assert object_keys() - previous_objects == {
        f"{workspace}/{replacement.json()['document_id']}/original.pdf"
    }


def test_worker_process_crash_recovers_durable_job(repo, pdf):
    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "crash.pdf", "crash")
    script = """
import sys,time
from uuid import UUID
from documind.api import repository
r=repository()
r.claim(UUID(sys.argv[1]))
print('claimed',flush=True)
time.sleep(60)
"""
    worker = subprocess.Popen(
        [sys.executable, "-c", script, str(row["job_id"])], stdout=subprocess.PIPE, text=True
    )
    assert worker.stdout.readline().strip() == "claimed"
    worker.kill()
    worker.wait(timeout=5)
    with repo.engine.begin() as c:
        c.execute(
            t.jobs.update()
            .where(t.jobs.c.id == row["job_id"])
            .values(lease_expires_at=now() - timedelta(seconds=1))
        )
    assert row["job_id"] in repo.due_jobs()
    from documind.worker import process_job

    process_job(row["job_id"])
    assert repo.job(repo.cfg.local_workspace_id, row["job_id"])["status"] == "succeeded"


def test_orphan_collection_preserves_references(repo, pdf):
    from pathlib import Path

    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "safe.pdf", "safe")
    key = f"{uuid4()}/{uuid4()}/orphan.pdf"
    repo.objects.put(key, b"orphan", "application/pdf")
    for obj in repo.objects.keys():
        os.utime(repo.objects.path(obj["Key"]), (0, 0))
    repo.collect_orphans()
    assert not Path(repo.objects.path(key)).exists()
    with repo.engine.connect() as c:
        original = c.execute(
            sa.select(t.documents.c.object_key).where(t.documents.c.id == row["document_id"])
        ).scalar_one()
    assert repo.objects.get(original, repo.cfg.max_upload_bytes).startswith(b"%PDF-")


def test_failed_cleanup_can_retry_after_revocation(repo, pdf):
    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "delete-fails")
    delete_id = repo.delete(repo.cfg.local_workspace_id, row["document_id"])
    job = repo.claim(delete_id)
    repo.fail(job, "DEPENDENCY_UNAVAILABLE", permanent=True)
    assert repo.job(repo.cfg.local_workspace_id, delete_id)["status"] == "failed"
    repo.retry(repo.cfg.local_workspace_id, delete_id, "retry-delete")
    from documind.worker import process_job

    process_job(delete_id)
    assert repo.job(repo.cfg.local_workspace_id, delete_id)["status"] == "succeeded"
    # A replay of the same retry request must not reopen terminal work.
    repo.retry(repo.cfg.local_workspace_id, delete_id, "retry-delete")
    assert repo.job(repo.cfg.local_workspace_id, delete_id)["status"] == "succeeded"


def test_promoted_deletion_and_http_pagination(client, repo, pdf):
    from documind.worker import process_job

    rows = [repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", f"page-{i}") for i in range(3)]
    base = f"/api/v1/workspaces/{repo.cfg.local_workspace_id}/documents"
    first = client.get(base + "?limit=2").json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get(base + "?limit=2&cursor=" + first["next_cursor"]).json()
    assert len(second["items"]) == 1 and not second["next_cursor"]
    process_job(rows[0]["job_id"])
    path = base + "/" + str(rows[0]["document_id"])
    deletion = client.delete(path)
    assert deletion.status_code == 202
    assert client.get(path + "/pages/1/image").status_code == 410
    process_job(UUID(deletion.json()["job_id"]))
    with repo.engine.connect() as c:
        assert (
            c.execute(
                sa.select(sa.func.count())
                .select_from(t.pages)
                .where(t.pages.c.document_id == rows[0]["document_id"])
            ).scalar_one()
            == 0
        )


def test_other_existing_workspace_never_exposes_sources(client, repo, pdf):
    from documind.repository import Repository
    from documind.worker import process_job

    other_id = uuid4()
    other = Repository(
        repo.engine, repo.objects, repo.cfg.model_copy(update={"local_workspace_id": other_id})
    )
    other.initialize()
    row = other.upload(other_id, pdf("PRIVATE WORKSPACE B"), "b.pdf", "b")
    process_job(row["job_id"])
    base = f"/api/v1/workspaces/{repo.cfg.local_workspace_id}/documents/{row['document_id']}"
    for suffix in ["", "/pages/1", "/pages/1/image", "/elements", "/original"]:
        response = client.get(base + suffix)
        assert response.status_code == 404
        assert "PRIVATE WORKSPACE B" not in response.text
    assert (
        client.get(base.replace(str(repo.cfg.local_workspace_id), str(other_id))).status_code == 404
    )


def test_old_completion_cannot_replace_newer_preview(repo, pdf):
    row = repo.upload(repo.cfg.local_workspace_id, pdf(), "a.pdf", "old-completion")
    old = repo.claim(row["job_id"])
    repo.cfg.max_extracted_chars = 1000000
    try:
        latest = repo.reprocess(repo.cfg.local_workspace_id, row["document_id"], "new-config")
        new = repo.claim(latest["job_id"])
        parsed = extract_pdf(pdf(), repo.parser_config(), 10)
        repo.promote(new, parsed)
        repo.promote(old, parsed)
        summary = repo.summary(repo.cfg.local_workspace_id, row["document_id"])
        assert summary["preview_ingestion_version"] == latest["latest_ingestion_version"]
    finally:
        repo.cfg.max_extracted_chars = 2000000
