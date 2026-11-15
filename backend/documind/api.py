import base64
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID, uuid4

import redis
import sqlalchemy as sa
from fastapi import Depends, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from documind.config import settings
from documind.db import engine, require_current_schema
from documind.errors import AppError
from documind.repository import Repository
from documind.storage import store

logger = logging.getLogger("documind")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = settings()  # Fail startup if local credentials or capability flags are invalid.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(cfg.log_level)
    logger.propagate = False
    yield


app = FastAPI(
    lifespan=lifespan,
    title="DocuMind document intelligence",
    version="0.1.0",
    dependencies=[Depends(HTTPBearer(auto_error=False))],
)


def repository() -> Repository:
    require_current_schema(engine())
    return Repository(engine(), store(), settings())


Repo = Annotated[Repository, Depends(repository)]


def error_payload(code, request_id):
    return {
        "error": {
            "code": code,
            "message": (
                "Run uv run --no-sync alembic upgrade head, then restart the app."
                if code == "DATABASE_MIGRATION_REQUIRED"
                else code.replace("_", " ").capitalize()
            ),
            "request_id": request_id,
            "details": {},
        }
    }


@app.middleware("http")
async def boundary(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.monotonic()
    try:
        if request.url.path not in {
            "/api/v1/health",
            "/docs",
            "/openapi.json",
            "/docs/oauth2-redirect",
        }:
            expected = f"Bearer {settings().local_api_token.get_secret_value()}"
            if not hmac.compare_digest(
                request.headers.get("authorization", "").encode(), expected.encode()
            ):
                raise AppError("UNAUTHENTICATED", 401)
        if request.method in {"POST", "PUT", "PATCH"}:
            # Bound the actual body, including chunked requests, before multipart parsing.
            upload_limit = settings().max_upload_bytes
            parts = request.url.path.rstrip("/").split("/")
            if (
                request.method == "POST"
                and len(parts) == 6
                and parts[:4] == ["", "api", "v1", "workspaces"]
                and parts[5] == "documents"
                and (key := request.headers.get("idempotency-key")) is not None
            ):
                try:
                    workspace_id = UUID(parts[4])
                except ValueError:
                    workspace_id = None  # FastAPI will validate the route parameter.
                if workspace_id is not None:
                    upload_limit = await run_in_threadpool(
                        repository().upload_limit, workspace_id, request_key(key)
                    )
            request.state.upload_limit = upload_limit
            limit = upload_limit + 65536
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > limit:
                    raise AppError("FILE_TOO_LARGE", 413)
                data.extend(chunk)
            request._body = bytes(data)
        response = await call_next(request)
    except AppError as exc:
        response = JSONResponse(error_payload(exc.code, request_id), status_code=exc.status)
    except Exception:
        logger.error(json.dumps({"event": "request_failed", "request_id": request_id}))
        response = JSONResponse(error_payload("INTERNAL_ERROR", request_id), status_code=500)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        )
    )
    return response


@app.exception_handler(AppError)
async def application_error(request: Request, exc: AppError):
    return JSONResponse(error_payload(exc.code, request.state.request_id), status_code=exc.status)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    code = "NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST"
    return JSONResponse(error_payload(code, request.state.request_id), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    return JSONResponse(error_payload("INVALID_REQUEST", request.state.request_id), status_code=422)


@app.get("/api/v1/health")
def health():
    return {"status": "alive"}


@app.get("/api/v1/ready")
def ready():
    schema_current = workspace_initialized = objects = broker = False
    schema_error = None
    ocr = False
    try:
        require_current_schema(engine())
        schema_current = True
        workspace_initialized = repository().workspace_exists()
    except AppError as exc:
        schema_error = exc.code
    except Exception:
        schema_error = "DATABASE_UNAVAILABLE"
    try:
        store().initialize()
        objects = store().root.is_dir()
    except Exception:
        pass
    try:
        broker = bool(
            redis.Redis.from_url(
                settings().redis_url, socket_connect_timeout=2, socket_timeout=2
            ).ping()
        )
    except Exception:
        pass
    if settings().ocr_enabled:
        try:
            from documind.ocr import model_identity

            model_identity(settings().ocr_model_root)
            ocr = True
        except Exception:
            pass
    native = workspace_initialized and objects and broker and (not settings().ocr_enabled or ocr)
    search = generation = False
    cfg = settings()
    if workspace_initialized and cfg.indexing_enabled:
        try:
            from documind.publications import active_id, checked_artifacts, publication
            from documind.search_models import retrieval_identity, search_artifacts_ready

            artifacts_ready = search_artifacts_ready()
            with engine().connect() as c:
                workspace = cfg.local_workspace_id
                identifier = active_id(c, workspace)
                item = publication(c, workspace, identifier) if identifier else None
            if item and artifacts_ready:
                checked_artifacts(repository(), item, retrieval_identity())
                with engine().connect() as c:
                    search = active_id(c, workspace) == identifier
        except Exception:
            search = False
    if workspace_initialized and cfg.generation_enabled:
        try:
            from documind import schema as t
            from documind.connections import secret_box

            secret_box(cfg)
            with engine().connect() as c:
                generation = bool(
                    c.scalar(
                        sa.select(t.connections.c.id)
                        .where(
                            t.connections.c.workspace_id == cfg.local_workspace_id,
                            t.connections.c.owner_principal_id == cfg.local_workspace_id,
                            t.connections.c.status == "ready",
                        )
                        .limit(1)
                    )
                )
        except Exception:
            generation = False
    trained = False
    split = False
    if workspace_initialized and broker and cfg.self_rag_enabled:
        from documind.runtime_client import admitted

        try:
            cap = admitted(cfg)
            trained = True
            split = cap.candidate_scoring
        except AppError:
            pass
    return JSONResponse(
        {
            "database_schema_current": schema_current,
            "error": error_payload(schema_error, None)["error"] if schema_error else None,
            "native_ingestion": native,
            "source_reads": workspace_initialized and objects,
            "workspace_initialized": workspace_initialized,
            "ocr": ocr,
            "tables": native and settings().tables_enabled,
            "review": workspace_initialized and objects,
            "search": search,
            "generation": generation,
            "self_rag": trained,
            "split_reflection": split,
            "app_location": "local",
            "reflection_location": cfg.reflection_location,
            "managed_kaggle": False,
        },
        status_code=200 if native else 503,
    )


@app.get("/api/v1/workspaces")
def workspaces(repo: Repo):
    if not repo.workspace_exists():
        raise AppError("WORKSPACE_NOT_INITIALIZED", 503)
    return {
        "items": [{"id": str(settings().local_workspace_id), "name": "Local workspace"}],
        "next_cursor": None,
    }


def cursor(value: str | None):
    if value is None:
        return None
    try:
        return UUID(base64.urlsafe_b64decode(value.encode()).decode())
    except Exception as exc:
        raise AppError("INVALID_CURSOR") from exc


def envelope(rows, overflow, *, key):
    return {
        "items": rows,
        "next_cursor": base64.urlsafe_b64encode(str(rows[-1][key]).encode()).decode()
        if overflow
        else None,
    }


def request_key(value):
    if not value or len(value) > 128 or not value.isascii() or any(ord(c) < 33 for c in value):
        raise AppError("INVALID_IDEMPOTENCY_KEY")
    return value


@app.post("/api/v1/workspaces/{workspace_id}/documents", status_code=202)
async def upload(
    workspace_id: UUID,
    request: Request,
    repo: Repo,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header()],
):
    repo.authorize(workspace_id)
    key = request_key(idempotency_key)
    limit = request.state.upload_limit
    data = await file.read(limit + 1)
    await file.close()
    if len(data) > limit:
        raise AppError("FILE_TOO_LARGE", 413)
    filename = (file.filename or "document.pdf").replace("\\", "/").split("/")[-1]
    if not filename.lower().endswith(".pdf") or len(filename) > 255:
        raise AppError("INVALID_DOCUMENT")
    return await run_in_threadpool(repo.upload, workspace_id, data, filename, key)


@app.get("/api/v1/workspaces/{workspace_id}/documents")
def documents(
    workspace_id: UUID,
    repo: Repo,
    limit: int = Query(50, ge=1, le=200),
    after: str | None = Query(None, alias="cursor"),
):
    return envelope(*repo.list_documents(workspace_id, limit, cursor(after)), key="document_id")


@app.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}")
def document(workspace_id: UUID, document_id: UUID, repo: Repo):
    return repo.summary(workspace_id, document_id)


@app.get("/api/v1/workspaces/{workspace_id}/jobs/{job_id}")
def job(workspace_id: UUID, job_id: UUID, repo: Repo):
    return repo.job(workspace_id, job_id)


@app.get("/api/v1/workspaces/{workspace_id}/cleanup-jobs")
def cleanup_jobs(
    workspace_id: UUID,
    repo: Repo,
    limit: int = Query(50, ge=1, le=200),
    after: str | None = Query(None, alias="cursor"),
):
    from documind.schema import jobs

    repo.authorize(workspace_id)
    statement = sa.select(jobs.c.id, jobs.c.status, jobs.c.error_code).where(
        jobs.c.workspace_id == workspace_id,
        jobs.c.kind == "delete",
        jobs.c.status.in_(("queued", "running", "failed")),
    )
    if after is not None:
        statement = statement.where(jobs.c.id > cursor(after))
    with repo.engine.connect() as c:
        rows = c.execute(statement.order_by(jobs.c.id).limit(limit + 1)).mappings().all()
    return envelope(rows[:limit], len(rows) > limit, key="id")


@app.post("/api/v1/workspaces/{workspace_id}/jobs/{job_id}/retry", status_code=202)
def retry(workspace_id: UUID, job_id: UUID, repo: Repo, idempotency_key: Annotated[str, Header()]):
    return repo.retry(workspace_id, job_id, request_key(idempotency_key))


@app.post("/api/v1/workspaces/{workspace_id}/documents/{document_id}/reprocess", status_code=202)
def reprocess(
    workspace_id: UUID, document_id: UUID, repo: Repo, idempotency_key: Annotated[str, Header()]
):
    return repo.reprocess(workspace_id, document_id, request_key(idempotency_key))


@app.delete("/api/v1/workspaces/{workspace_id}/documents/{document_id}", status_code=202)
def delete(workspace_id: UUID, document_id: UUID, repo: Repo):
    return {"job_id": repo.delete(workspace_id, document_id)}


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    element_id: UUID
    text_raw: str = Field(max_length=100000)
    is_header: bool | None = None


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_ingestion_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_all_pages: Literal[True]
    corrections: list[Correction] = Field(default_factory=list, max_length=1000)


@app.post("/api/v1/workspaces/{workspace_id}/documents/{document_id}/review", status_code=202)
def review_source(
    workspace_id: UUID,
    document_id: UUID,
    body: Review,
    repo: Repo,
    idempotency_key: Annotated[str, Header()],
):
    return repo.reprocess(
        workspace_id, document_id, request_key(idempotency_key), review=body.model_dump(mode="json")
    )


@app.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}")
def page(
    workspace_id: UUID,
    document_id: UUID,
    page_number: int,
    repo: Repo,
    ingestion_version: str | None = Query(None, max_length=64),
):
    if page_number < 1:
        raise AppError("INVALID_PAGE", 422)
    rows = repo.source(workspace_id, document_id, ingestion_version, page_number)
    if not rows:
        raise AppError("NOT_FOUND", 404)
    result = rows[0]
    try:
        observed_size = repo.objects.source_size(result.pop("render_object_key"))
        if result["render_size_bytes"] not in (None, observed_size):
            raise AppError("SOURCE_HASH_MISMATCH", 503)
    finally:
        repo.summary(workspace_id, document_id)
    result.pop("render_size_bytes")
    result.pop("render_sha256")
    result["image_url"] = (
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/pages/"
        f"{page_number}/image?ingestion_version={result['ingestion_version']}"
    )
    return result


@app.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}/image")
def image(
    workspace_id: UUID,
    document_id: UUID,
    page_number: int,
    repo: Repo,
    ingestion_version: str | None = Query(None, max_length=64),
):
    rows = repo.source(workspace_id, document_id, ingestion_version, page_number)
    if not rows:
        raise AppError("NOT_FOUND", 404)
    try:
        data = repo.objects.read_preview(rows[0])
    finally:
        repo.summary(workspace_id, document_id)
    return Response(data, media_type="image/png")


@app.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/original")
def original(workspace_id: UUID, document_id: UUID, repo: Repo):
    data = repo.original(workspace_id, document_id)
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="document.pdf"'},
    )


@app.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/elements")
def elements(
    workspace_id: UUID,
    document_id: UUID,
    repo: Repo,
    ingestion_version: str | None = Query(None, max_length=64),
    page_number: int | None = Query(None, ge=1),
    element_type: Literal["paragraph", "table", "table_cell"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    after: str | None = Query(None, alias="cursor"),
):
    rows, overflow = repo.element_list(
        workspace_id,
        document_id,
        ingestion_version,
        page_number,
        limit,
        cursor(after),
        element_type,
    )
    for row in rows:
        row["element_id"] = row["id"]
        row["text"] = row["text_raw"]
    return envelope(rows, overflow, key="id")


def register_phase2():
    from documind.investigation_api import router as investigations
    from documind.phase2_api import router

    app.include_router(router)
    app.include_router(investigations)


register_phase2()
