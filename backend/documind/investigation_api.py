"""Local workspace routes for Phase 4 comparisons and reports."""

from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from documind import investigations
from documind import schema as t
from documind.api import Repo, cursor, envelope, request_key

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}")
RequestKey = Annotated[str, Header(alias="Idempotency-Key")]


class InvestigationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="Invoice and purchase order", min_length=1, max_length=120)
    document_ids: list[UUID] = Field(min_length=2, max_length=3)


class ReportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["csv"] = "csv"


@router.post("/investigations", status_code=201)
def create_investigation(
    workspace_id: UUID,
    body: InvestigationBody,
    repo: Repo,
    idempotency_key: RequestKey,
):
    return investigations.create(
        repo, workspace_id, body.name, body.document_ids, request_key(idempotency_key)
    )


@router.get("/investigations")
def list_investigations(
    workspace_id: UUID,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    after: str | None = Query(None, alias="cursor"),
):
    repo.authorize(workspace_id)
    table = t.investigations
    statement = sa.select(
        table.c.id.label("investigation_id"), table.c.name, table.c.created_at, table.c.job_id
    ).where(table.c.workspace_id == workspace_id, table.c.revoked_at.is_(None))
    if after is not None:
        statement = statement.where(table.c.id > cursor(after))
    with repo.engine.connect() as c:
        rows = c.execute(statement.order_by(table.c.id).limit(limit + 1)).mappings().all()
        return envelope(rows[:limit], len(rows) > limit, key="investigation_id")


@router.post("/investigations/{identifier}/run", status_code=202)
def run_investigation(
    workspace_id: UUID,
    identifier: UUID,
    repo: Repo,
    idempotency_key: RequestKey,
):
    return {
        "job_id": investigations.start(repo, workspace_id, identifier, request_key(idempotency_key))
    }


@router.get("/investigations/{identifier}")
def get_investigation(workspace_id: UUID, identifier: UUID, repo: Repo):
    return investigations.detail(repo, workspace_id, identifier)


@router.get("/investigations/{identifier}/findings")
def get_findings(workspace_id: UUID, identifier: UUID, repo: Repo):
    return {"items": investigations.detail(repo, workspace_id, identifier)["findings"]}


@router.get("/investigations/{identifier}/evidence/{document}/{field}")
def get_evidence(workspace_id: UUID, identifier: UUID, document: UUID, field: str, repo: Repo):
    return investigations.evidence(repo, workspace_id, identifier, document, field)


@router.post("/investigations/{identifier}/reports", status_code=202)
def create_report(
    workspace_id: UUID,
    identifier: UUID,
    body: ReportBody,
    repo: Repo,
    idempotency_key: RequestKey,
):
    report_id = investigations.start_report(
        repo, workspace_id, identifier, request_key(idempotency_key)
    )
    return {"job_id": report_id, "report_id": report_id, "format": body.format}


@router.get("/reports/{report_id}")
def get_report(workspace_id: UUID, report_id: UUID, repo: Repo):
    return investigations.report(repo, workspace_id, report_id)


@router.get("/reports/{report_id}/content")
def download_report(workspace_id: UUID, report_id: UUID, repo: Repo):
    data = investigations.report(repo, workspace_id, report_id, download=True)
    return Response(
        data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="documind-comparison.csv"'},
    )
