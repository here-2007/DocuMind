"""Regressions for the three Phase 0/1A review findings."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pymupdf
import pytest
from sqlalchemy import event

pytestmark = pytest.mark.integration


def prepare(repo):
    from documind.worker import process_job

    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=400)
        for index in range(3):
            page.insert_text((40, 60 + index * 80), f"Synthetic block {index}")
        data = doc.tobytes()
    row = repo.upload(repo.cfg.local_workspace_id, data, "review.pdf", "review")
    process_job(row["job_id"])
    return row, f"/api/v1/workspaces/{repo.cfg.local_workspace_id}/documents/{row['document_id']}"


def test_element_pagination_visits_every_element_once(client, repo):
    _, path = prepare(repo)
    expected = {r["id"] for r in client.get(path + "/elements").json()["items"]}
    assert len(expected) == 3
    seen = []
    after = None
    for _ in range(4):
        params = {"limit": 1}
        if after:
            params["cursor"] = after
        response = client.get(path + "/elements", params=params)
        assert response.status_code == 200
        result = response.json()
        seen.extend(r["id"] for r in result["items"])
        after = result["next_cursor"]
        if not after:
            break
    assert after is None and len(seen) == len(set(seen)) == len(expected)
    assert set(seen) == expected


@pytest.mark.parametrize(
    ("suffix", "table"), [("/pages/1", "document_pages"), ("/elements", "document_elements")]
)
def test_deletion_during_text_read_revokes_response(client, repo, suffix, table):
    row, path = prepare(repo)
    deleted = []

    def revoke(conn, cursor, statement, parameters, context, executemany):
        if not deleted and statement.lstrip().startswith("SELECT") and f"FROM {table}" in statement:
            deleted.append(True)
            repo.delete(repo.cfg.local_workspace_id, row["document_id"])

    event.listen(repo.engine, "before_cursor_execute", revoke)
    try:
        response = client.get(path + suffix)
    finally:
        event.remove(repo.engine, "before_cursor_execute", revoke)
    assert deleted
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "SOURCE_UNAVAILABLE"
    assert "Synthetic block" not in response.text


def test_real_uvicorn_emits_safe_request_record(runtime, tmp_path):
    # A passed-in socket avoids competing with another process for the chosen port.
    with socket.socket() as listener, (tmp_path / "api.log").open("w+") as output:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "documind.api:app",
                "--fd",
                str(listener.fileno()),
                "--no-access-log",
            ],
            pass_fds=(listener.fileno(),),
            env={**os.environ, "LOG_LEVEL": "INFO"},
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(trust_env=False) as client:
                while True:
                    try:
                        response = client.get(
                            f"http://127.0.0.1:{port}/api/v1/health?secret=private-document-marker",
                            headers={"Authorization": "Bearer private-token-marker"},
                        )
                        break
                    except httpx.TransportError:
                        assert time.monotonic() < deadline, "Uvicorn did not start"
                        time.sleep(0.1)
                assert response.status_code == 200
        finally:
            proc.terminate()
            proc.wait(timeout=15)
        output.seek(0)
        logs = output.read()
    records = [json.loads(line) for line in logs.splitlines() if line.startswith('{"event"')]
    matches = [r for r in records if r.get("request_id") == response.headers["X-Request-ID"]]
    assert len(matches) == 1 and matches[0]["event"] == "http_request"
    assert matches[0]["status"] == 200
    assert "private-token-marker" not in logs and "private-document-marker" not in logs


def test_lease_recovery_uses_same_clock_as_lease_writes(repo, monkeypatch):
    from datetime import timedelta

    from documind import schema as t
    from documind.repository import now

    # Docker's PostgreSQL clock can lag the host clock used by lease writes.
    clock = now() + timedelta(seconds=60)
    monkeypatch.setattr("documind.repository.now", lambda: clock)
    row, _ = prepare(repo)
    # prepare completed its first job; a fresh queued job exercises recovery.
    monkeypatch.setattr(repo.cfg, "max_extracted_chars", repo.cfg.max_extracted_chars - 1)
    fresh = repo.reprocess(repo.cfg.local_workspace_id, row["document_id"], "clock-reprocess")
    repo.claim(fresh["job_id"])
    with repo.engine.begin() as connection:
        connection.execute(
            t.jobs.update()
            .where(t.jobs.c.id == fresh["job_id"])
            .values(lease_expires_at=clock - timedelta(seconds=1))
        )
    assert fresh["job_id"] in repo.due_jobs()
    current = repo.claim(fresh["job_id"])
    assert current is not None
    monkeypatch.setattr("documind.repository.now", lambda: clock + timedelta(seconds=61))
    from documind.errors import AppError

    with pytest.raises(AppError, match="LEASE_LOST"):
        repo.heartbeat(current)
