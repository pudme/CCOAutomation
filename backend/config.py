from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://compliance:password@localhost:5432/compliance_db",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Document store
    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="compliance", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="password", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="compliance-documents", alias="MINIO_BUCKET")

    # Vector DB
    chroma_host: str = Field(default="localhost", alias="CHROMA_HOST")
    chroma_port: int = Field(default=8001, alias="CHROMA_PORT")

    # AI
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model_sonnet: str = Field(
        default="claude-sonnet-4-5-20250929",
        alias="ANTHROPIC_MODEL_SONNET",
    )
    anthropic_model_haiku: str = Field(
        default="claude-haiku-4-5-20251001",
        alias="ANTHROPIC_MODEL_HAIKU",
    )

    # App
    app_env: str = Field(default="development", alias="APP_ENV")
    audit_date: str = Field(default="2026-05-15", alias="AUDIT_DATE")
    # Local write-gate stopgap (not Cognito). Empty = writes unrestricted (dev convenience).
    ccoa_dev_key: str = Field(default="", alias="CCOA_DEV_KEY")
    # Comma-separated browser origins allowed by CORS (default host UI port 3001).
    cors_origins: str = Field(default="http://localhost:3001", alias="CORS_ORIGINS")
    # Public base URL for generated report download links.
    public_api_base_url: str = Field(default="http://localhost:8010", alias="PUBLIC_API_BASE_URL")

    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in (self.cors_origins or "").split(",") if part.strip()]

    # Local evidence drop watcher (bind-mounted folder)
    evidence_watch_path: str = Field(default="/app/evidence-drop", alias="EVIDENCE_WATCH_PATH")
    evidence_watch_interval_seconds: int = Field(default=60, alias="EVIDENCE_WATCH_INTERVAL_SECONDS")
    evidence_watch_library: str = Field(default="main", alias="EVIDENCE_WATCH_LIBRARY")
    evidence_watch_enabled: bool = Field(default=True, alias="EVIDENCE_WATCH_ENABLED")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if isinstance(value, str) and value.startswith("postgresql://") and "+asyncpg" not in value:
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if isinstance(value, str) and value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def normalize_anthropic_api_key(cls, value: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip()
        return ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

