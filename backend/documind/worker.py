import hashlib
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
        "visibility_timeout": 300,
        "global_keyprefix": cfg.celery_queue + ":",
    },
    task_time_limit=cfg.parser_timeout_seconds + 90,
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
        data = store().get(job["object_key"], settings().max_upload_bytes)
        if hashlib.sha256(data).hexdigest() != job["sha256"]:
            raise AppError("SOURCE_HASH_MISMATCH", 422)
        parsed = extract_pdf(
            data,
            version["config_json"],
            settings().parser_timeout_seconds,
            heartbeat=lambda: repo.heartbeat(job),
        )
        repo.promote(job, parsed)
    except AppError as exc:
        if exc.code != "LEASE_LOST":
            repo.fail(job, exc.code, permanent=True)
    except Exception:
        # Driver/parser exception strings may contain private data or credentials.
        logger.error('{"event":"job_attempt_failed","job_id":"%s"}', job_id)
        repo.fail(job, "DEPENDENCY_UNAVAILABLE")


@celery.task(name="documind.process_job")
def ingest(job_id: str):
    process_job(UUID(job_id))


def dispatch_once() -> int:
    repo = Repository(engine(), store(), settings())
    count = 0
    for job_id in repo.due_jobs():
        try:
            ingest.apply_async(args=[str(job_id)])
            count += 1
        except Exception:
            logger.warning('{"event":"broker_unavailable"}')
            break
    return count
