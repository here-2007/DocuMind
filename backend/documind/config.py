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
    indexing_enabled: bool = False
    generation_enabled: bool = False
    self_rag_enabled: bool = False
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
            raise ValueError("This slice supports local-only deployment")
        if len(self.local_api_token.get_secret_value()) < 24:
            raise ValueError("LOCAL_API_TOKEN must contain at least 24 characters")
        if self.job_heartbeat_seconds >= self.job_lease_seconds / 2:
            raise ValueError("Heartbeat must be less than half the lease")
        if any(
            (
                self.indexing_enabled,
                self.generation_enabled,
                self.self_rag_enabled,
                self.managed_kaggle_enabled,
            )
        ):
            raise ValueError("Search, inference and managed Kaggle are not implemented")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
