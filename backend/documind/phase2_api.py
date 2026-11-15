"""Phase 2 endpoints. Authentication stays at the shared FastAPI boundary."""

from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.concurrency import run_in_threadpool

from documind import connections, publications, queries
from documind import schema as t
from documind.api import Repo, cursor, envelope, request_key
from documind.errors import AppError
from documind.query_stream import accepts_stream, event_cursor, expired_events, query_stream

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}")
RequestKey = Annotated[str, Header(alias="Idempotency-Key")]


class ConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["custom_endpoint"] = "custom_endpoint"
    protocol: Literal["openai_chat_completions"] = "openai_chat_completions"
    display_name: str = Field(min_length=1, max_length=80)
    endpoint_url: str = Field(min_length=1, max_length=512)
    model_id: str = Field(min_length=1, max_length=256)
    api_key: SecretStr | None = Field(
        default=None, exclude=True, json_schema_extra={"writeOnly": True}
    )

    @field_validator("api_key")
    @classmethod
    def valid_credential(cls, value):
        if value is not None:
            secret = value.get_secret_value()
            if not 0 < len(secret) <= 4096 or any(not 33 <= ord(c) <= 126 for c in secret):
                raise ValueError("Invalid endpoint credential")
        return value

    def payload(self):
        return {
            **self.model_dump(),
            "api_key": self.api_key.get_secret_value() if self.api_key else None,
        }


class ConnectionEdit(ConnectionBody):
    expected_config_version: int = Field(ge=1)


class QueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1024)
    document_ids: list[UUID] | None = Field(default=None, max_length=200)
    mode: Literal["auto", "baseline_rag", "self_rag", "deterministic_comparison"] = "auto"
    connection_id: UUID | None = None
    expected_config_version: int | None = Field(default=None, ge=1)


@router.post("/publications", status_code=202)
def start_publication(workspace_id: UUID, repo: Repo, idempotency_key: RequestKey):
    identifier = publications.start_publication(repo, workspace_id, request_key(idempotency_key))
    return {"publication_id": identifier, "job_id": identifier}


@router.get("/publications/{publication_id}")
def publication_status(workspace_id: UUID, publication_id: UUID, repo: Repo):
    repo.authorize(workspace_id)
    with repo.engine.connect() as c:
        item = publications.publication(c, workspace_id, publication_id)
        return {key: item[key] for key in ("id", "status", "parent_id", "manifest", "revoked_at")}


@router.post("/inference-connections", status_code=201)
def create_connection(
    workspace_id: UUID,
    body: ConnectionBody,
    repo: Repo,
    idempotency_key: RequestKey,
):
    return connections.write_connection(
        repo, workspace_id, body.payload(), request_key(idempotency_key)
    )


@router.get("/inference-connections")
def list_connections(
    workspace_id: UUID,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    after: Annotated[str | None, Query(alias="cursor")] = None,
):
    repo.authorize(workspace_id)
    with repo.engine.connect() as c:
        statement = sa.select(t.connections).where(
            t.connections.c.workspace_id == workspace_id,
            t.connections.c.owner_principal_id == workspace_id,
        )
        if after is not None:
            statement = statement.where(t.connections.c.id > cursor(after))
        rows = c.execute(statement.order_by(t.connections.c.id).limit(limit + 1)).mappings().all()
        return envelope(
            [connections.projection(c, row) for row in rows[:limit]],
            len(rows) > limit,
            key="id",
        )


@router.get("/inference-connections/{connection_id}")
def get_connection(workspace_id: UUID, connection_id: UUID, repo: Repo):
    repo.authorize(workspace_id)
    with repo.engine.connect() as c:
        return connections.projection(c, connections.connection(c, workspace_id, connection_id))


@router.patch("/inference-connections/{connection_id}")
def edit_connection(
    workspace_id: UUID,
    connection_id: UUID,
    body: ConnectionEdit,
    repo: Repo,
    idempotency_key: RequestKey,
):
    return connections.write_connection(
        repo,
        workspace_id,
        body.payload(),
        request_key(idempotency_key),
        connection_id,
    )


@router.post("/inference-connections/{connection_id}/validate", status_code=202)
def validate_connection(
    workspace_id: UUID,
    connection_id: UUID,
    repo: Repo,
    idempotency_key: RequestKey,
):
    return {
        "job_id": connections.queue_validation(
            repo,
            workspace_id,
            connection_id,
            request_key(idempotency_key),
        )
    }


@router.delete("/inference-connections/{connection_id}", status_code=204)
def revoke_connection(workspace_id: UUID, connection_id: UUID, repo: Repo):
    connections.revoke(repo, workspace_id, connection_id)
    return Response(status_code=204)


@router.post("/queries", status_code=202)
def create_query(workspace_id: UUID, body: QueryBody, repo: Repo, idempotency_key: RequestKey):
    return queries.create_query(repo, workspace_id, body.model_dump(), request_key(idempotency_key))


@router.get("/queries/{query_id}")
def query_result(workspace_id: UUID, query_id: UUID, repo: Repo):
    return queries.get_result(repo, workspace_id, query_id)


@router.post("/queries/{query_id}/cancel")
def cancel_query(workspace_id: UUID, query_id: UUID, repo: Repo):
    return queries.cancel(repo, workspace_id, query_id)


@router.get("/queries/{query_id}/events")
async def query_events(
    workspace_id: UUID,
    query_id: UUID,
    request: Request,
    repo: Repo,
    after_seq: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    after = event_cursor(after_seq, request.headers.get("last-event-id"))
    try:
        if accepts_stream(request.headers.get("accept", "")):
            return await query_stream(request, repo, workspace_id, query_id, after, limit)
        return await run_in_threadpool(queries.events, repo, workspace_id, query_id, after, limit)
    except AppError as exc:
        if exc.code != "EVENTS_EXPIRED":
            raise
        return expired_events(request, f"/api/v1/workspaces/{workspace_id}/queries/{query_id}")


@router.get("/citations/{citation_id}")
def citation_source(workspace_id: UUID, citation_id: UUID, repo: Repo):
    return queries.citation_source(repo, workspace_id, citation_id)
