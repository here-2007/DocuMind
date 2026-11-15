import logging
from uuid import UUID

from celery import Celery

from documind import schema as t
from documind.config import settings
from documind.db import engine
from documind.errors import AppError
from documind.parser import PARSER_VERSION, REVIEW_VERSION, extract_pdf
from documind.repository import Repository
from documind.storage import store

cfg = settings()
celery = Celery("documind", broker=cfg.redis_url)
celery.conf.update(
    task_default_queue=cfg.celery_queue,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": 600,
        "global_keyprefix": cfg.celery_queue + ":",
    },
    task_time_limit=max(cfg.parser_timeout_seconds + 90, cfg.query_timeout_seconds + 15, 540),
    broker_connection_timeout=3,
    broker_connection_max_retries=0,
    task_publish_retry=False,
)
logger = logging.getLogger("documind")


def process_job(job_id: UUID):
    repo = Repository(engine(), store(), settings())
    job = repo.claim(job_id)
    if not job:
        return
    try:
        if job["kind"] == "publication":
            from documind.publications import build_publication

            build_publication(repo, job)
            return
        if job["kind"] == "query":
            from documind.queries import process_query

            process_query(repo, job)
            return
        if job["kind"] == "validate":
            from documind.connections import validate_job

            validate_job(repo, job)
            return
        if job["kind"] in {"comparison", "report"}:
            from documind.investigations import process_comparison, process_report

            operation = process_comparison if job["kind"] == "comparison" else process_report
            operation(repo, job)
            return
        if job["kind"] == "delete":
            repo.cleanup_document(job)
            return
        with engine().connect() as c:
            version = (
                c.execute(
                    t.versions.select().where(
                        t.versions.c.workspace_id == job["workspace_id"],
                        t.versions.c.document_id == job["document_id"],
                        t.versions.c.ingestion_version == job["ingestion_version"],
                    )
                )
                .mappings()
                .one()
            )
        if version["parser_version"] == REVIEW_VERSION:
            from documind.review import reviewed_pages

            repo.promote(job, reviewed_pages(repo, job, version["config_json"]))
            return
        if version["parser_version"] != PARSER_VERSION:
            raise AppError("PARSER_VERSION_UNAVAILABLE", 409)
        data = repo.objects.read_source(
            job["object_key"], size=job["original_size_bytes"], sha256=job["sha256"]
        )
        parsed = extract_pdf(
            data,
            version["config_json"],
            settings().parser_timeout_seconds,
            heartbeat=lambda: repo.heartbeat(job),
        )
        repo.promote(job, parsed)
    except AppError as exc:
        if exc.code != "LEASE_LOST":
            retryable = exc.code == "PUBLICATION_BUSY" or (
                job["kind"] in {"delete", "publication"} and exc.code == "DENSE_INDEX_UNAVAILABLE"
            )
            repo.fail(job, exc.code, permanent=not retryable)
    except Exception:
        # Driver/parser exception strings may contain private data or credentials.
        logger.error('{"event":"job_attempt_failed","job_id":"%s"}', job_id)
        repo.fail(job, "DEPENDENCY_UNAVAILABLE", permanent=job["kind"] in {"query", "validate"})


@celery.task(name="documind.process_job")
def ingest(job_id: str):
    process_job(UUID(job_id))


def dispatch_once() -> int:
    repo = Repository(engine(), store(), settings())
    count = 0
    for job_id in repo.due_jobs():
        if not repo.reserve_dispatch(job_id):
            continue
        try:
            ingest.apply_async(args=[str(job_id)])
            count += 1
        except Exception:
            logger.warning('{"event":"broker_unavailable"}')
            break
    return count
