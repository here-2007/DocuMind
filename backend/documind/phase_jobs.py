"""Phase 2 work uses the existing PostgreSQL job lease and Celery dispatcher."""

from uuid import uuid4

import sqlalchemy as sa

from documind import schema as t
from documind.errors import AppError
from documind.repository import now


def request(c, workspace, operation, key, digest):
    lock_workspace(c, workspace)
    previous = (
        c.execute(
            sa.select(t.phase_requests).where(
                t.phase_requests.c.workspace_id == workspace,
                t.phase_requests.c.principal_id == workspace,
                t.phase_requests.c.operation == operation,
                t.phase_requests.c.key == key,
            )
        )
        .mappings()
        .first()
    )
    if previous and previous["request_hash"] != digest:
        raise AppError("IDEMPOTENCY_CONFLICT", 409)
    return previous


def record_request(
    c, workspace, operation, key, digest, resource_id, job_id=None, *, response=None
):
    c.execute(
        t.phase_requests.insert().values(
            workspace_id=workspace,
            principal_id=workspace,
            operation=operation,
            key=key,
            request_hash=digest,
            resource_id=resource_id,
            job_id=job_id,
            response=response,
        )
    )


def lock_workspace(c, workspace):
    c.execute(
        sa.select(t.workspaces.c.id).where(t.workspaces.c.id == workspace).with_for_update()
    ).scalar_one()


def queue(c, workspace, kind):
    identifier = uuid4()
    c.execute(
        t.jobs.insert().values(
            id=identifier,
            workspace_id=workspace,
            kind=kind,
            idempotency_key=str(identifier),
            next_attempt_at=now(),
        )
    )
    return identifier


def terminal(c, job, status, code=None):
    c.execute(
        t.jobs.update()
        .where(t.jobs.c.id == job["id"])
        .values(
            status=status,
            error_code=code,
            finished_at=now(),
            lease_token=None,
            lease_expires_at=None,
        )
    )
    if job["kind"] == "query":
        c.execute(
            t.query_runs.update()
            .where(
                t.query_runs.c.workspace_id == job["workspace_id"],
                t.query_runs.c.id == job["id"],
                t.query_runs.c.runtime_progress.is_not(None),
            )
            .values(runtime_progress=None)
        )


def query_can_resume(run):
    if not run["generation_started"]:
        return True
    progress = run["runtime_progress"] if run["mode"] == "self_rag" else None
    steps = progress.get("steps") if isinstance(progress, dict) else None
    return bool(
        isinstance(steps, list)
        and steps
        and all(isinstance(step, dict) and isinstance(step.get("result"), dict) for step in steps)
        and ("retrieval" not in progress or isinstance(progress["retrieval"], list))
    )


def event(c, workspace, query, event_type, **payload):
    seq = (
        c.scalar(
            sa.select(sa.func.coalesce(sa.func.max(t.query_events.c.seq), 0)).where(
                t.query_events.c.workspace_id == workspace,
                t.query_events.c.query_id == query,
            )
        )
        + 1
    )
    c.execute(
        t.query_events.insert().values(
            workspace_id=workspace,
            query_id=query,
            seq=seq,
            event_type=event_type,
            payload=payload,
        )
    )


def fail(c, job, code):
    workspace, identifier = job["workspace_id"], job["id"]
    terminal(c, job, "failed", code)
    if job["kind"] == "query":
        run = c.execute(
            sa.select(t.query_runs).where(
                t.query_runs.c.workspace_id == workspace,
                t.query_runs.c.id == identifier,
            )
        ).one()
        if run.revoked_at is None:
            event(c, workspace, identifier, "error", code=code)
        if run.generation_started and code in {
            "INFERENCE_MODEL_CHANGED",
            "CONNECTION_UNAVAILABLE",
            "INFERENCE_QUOTA_EXCEEDED",
            "INVALID_PROVIDER_RESPONSE",
            "ENDPOINT_NOT_ALLOWED",
            "ENDPOINT_REDIRECT_REJECTED",
        }:
            c.execute(
                t.connections.update()
                .where(
                    t.connections.c.workspace_id == workspace,
                    t.connections.c.id == run.connection_id,
                    t.connections.c.config_version == run.config_version,
                    t.connections.c.check_id == run.check_id,
                    t.connections.c.status == "ready",
                )
                .values(status="unavailable", error_code=code)
            )
    elif job["kind"] == "publication":
        c.execute(
            t.publications.update()
            .where(
                t.publications.c.workspace_id == workspace,
                t.publications.c.id == identifier,
                t.publications.c.status == "building",
            )
            .values(status="failed")
        )
    elif job["kind"] == "validate":
        c.execute(
            t.connections.update()
            .where(
                t.connections.c.workspace_id == workspace,
                t.connections.c.check_id == identifier,
                t.connections.c.status != "revoked",
            )
            .values(status="unavailable", error_code=code)
        )
