from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_env: str = "local"
    auth_mode: str = "local_only"
    api_host: str = "127.0.0.1"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    local_api_token: SecretStr
    local_workspace_id: UUID = UUID("3e34d85d-2ebe-4141-8024-cbfe5feec000")
    database_url: SecretStr
    celery_queue: str = "documind"
    redis_url: str = "redis://127.0.0.1:56379/0"
    object_store_root: Path = Path(".data/objects")
    max_upload_bytes: int = Field(default=20971520, gt=0)
    max_workspace_documents: int = Field(default=200, gt=0)
    max_pdf_pages: int = Field(default=100, gt=0)
    max_page_pixels: int = Field(default=12000000, gt=0)
    max_document_pixels: int = Field(default=24000000, gt=0)
    max_rendered_bytes: int = Field(default=64000000, gt=0)
    max_extracted_chars: int = Field(default=2000000, gt=0)
    parser_timeout_seconds: int = Field(default=90, gt=0)
    job_max_attempts: int = Field(default=5, gt=0)
    job_lease_seconds: int = Field(default=60, ge=3)
    job_heartbeat_seconds: int = Field(default=15, gt=0)
    job_retry_base_seconds: int = Field(default=2, gt=0)
    job_retry_max_seconds: int = Field(default=60, gt=0)
    dispatch_poll_seconds: int = Field(default=5, gt=0)
    orphan_grace_seconds: int = Field(default=86400, ge=60)
    qdrant_url: str = "http://127.0.0.1:56333"
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = Field(default="documind_chunks_v3", pattern=r"^[a-zA-Z0-9_-]+$")
    max_publication_chunks: int = Field(default=10000, ge=1, le=20000)
    max_query_documents: int = Field(default=50, ge=1, le=200)
    query_event_retention_days: int = Field(default=30, ge=1, le=365)
    query_timeout_seconds: int = Field(default=120, ge=5, le=300)
    inference_timeout_seconds: int = Field(default=15, ge=1, le=30)
    inference_secret_key: SecretStr | None = None
    local_chat_endpoint: str | None = None
    indexing_enabled: bool = False
    generation_enabled: bool = False
    self_rag_enabled: bool = False
    reflection_location: Literal["local", "kaggle"] = "local"
    self_rag_runtime_origin: str | None = None
    self_rag_runtime_token: SecretStr | None = None
    self_rag_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    self_rag_inference_profile: str | None = None
    managed_kaggle_enabled: bool = False
    ocr_enabled: bool = False
    ocr_model_root: Path = Path(".data/models/ocr")
    ocr_dpi: int = Field(default=144, ge=72, le=300)
    ocr_min_native_chars: int = Field(default=8, ge=0)
    ocr_language: Literal["en"] = "en"
    tables_enabled: bool = True
    max_table_cells: int = Field(default=1000, gt=0, le=10000)

    @model_validator(mode="after")
    def local_slice(self):
        if self.app_env != "local" or self.auth_mode != "local_only":
            raise ValueError("DocuMind is a local-only application")
        if self.api_host not in {"127.0.0.1", "::1"}:
            raise ValueError("The local API host must be a literal loopback address")
        if len(self.local_api_token.get_secret_value()) < 24:
            raise ValueError("LOCAL_API_TOKEN must contain at least 24 characters")
        if self.job_heartbeat_seconds >= self.job_lease_seconds / 2:
            raise ValueError("Heartbeat must be less than half the lease")
        if self.indexing_enabled and (
            not self.qdrant_api_key or len(self.qdrant_api_key.get_secret_value()) < 24
        ):
            raise ValueError("Indexing needs a private QDRANT_API_KEY of at least 24 characters")
        if self.managed_kaggle_enabled:
            raise ValueError("Managed Kaggle is unavailable")
        if self.self_rag_enabled:
            from documind.errors import AppError
            from documind.runtime_client import CAPABILITIES, endpoint

            token = self.self_rag_runtime_token
            secret = token.get_secret_value() if token else ""
            if (
                not self.self_rag_manifest_sha256
                or len(secret) < 24
                or not secret.isascii()
                or any(char.isspace() for char in secret)
            ):
                raise ValueError("Self-RAG needs an approved manifest and private runtime token")
            try:
                endpoint(self.self_rag_runtime_origin, CAPABILITIES)
            except (AppError, ValueError) as exc:
                raise ValueError("Self-RAG needs an approved runtime origin") from exc
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
