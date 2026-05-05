# shared/config/settings.py
"""
File Use:
    Centralized application settings for all services. Loads configuration
    from environment variables and .env file using pydantic-settings.

Implements:
    - MongoSettings (MongoDB connection config)
    - MinioSettings (MinIO object storage config)
    - ApiSettings (API server config)
    - LiveKitSettings (LiveKit SFU connection config)
    - AppSettings (top-level application settings)

Depends On:
    - pydantic-settings

Used By:
    - main.py
    - mongo_store.py
    - shared/storage/minio_client.py
    - livekit_service/livekit_publisher.py
"""

from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class MongoSettings(BaseModel):
    """MongoDB connection configuration."""

    uri: str = "mongodb://localhost:27017"
    database: str = "nexa_security"


class MinioSettings(BaseModel):
    """MinIO object storage configuration."""

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    secure: bool = False
    bucket: str = "nexa-evidence"


class RedisSettings(BaseModel):
    """Redis connection configuration (used by external inference workers)."""

    url: str = "redis://localhost:6379"


class ApiSettings(BaseModel):
    """API server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000


class LiveKitSettings(BaseModel):
    """LiveKit SFU connection configuration.

    Room naming: room name = tenant_id (one room per tenant).
    Each camera is a separate participant with track name = camera_id.
    """

    url: str = "ws://localhost:7880"
    public_url: str | None = None
    api_key: str = "devkey"
    api_secret: str = "dummy-key-for-dev"
    publish_fps: int = 15
    publish_width: int = 640
    publish_height: int = 480
    # Token lifetime (seconds) for publisher/subscriber JWTs.
    token_ttl_sec: int = 3600


class AuthSettings(BaseModel):
    """JWT authentication configuration."""

    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    min_password_length: int = 8


class AppSettings(BaseSettings):
    """Top-level application settings.

    Responsibilities:
        - Aggregate all subsystem configurations.
        - Load from environment variables and .env file.
        - Provide sensible MVP defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cors_allowed_origins: list[str] = ["*"]

    # Alert evidence settings (single source of truth — read by main.py at startup)
    alert_clip_enabled: bool = True
    alert_snapshot_enabled: bool = True
    alert_snapshot_dir: str = "alerts"
    alert_clip_fps: int = 5
    alert_frames_before: int = 15
    alert_frames_after: int = 15

    mongo: MongoSettings = MongoSettings()
    minio: MinioSettings = MinioSettings()
    redis: RedisSettings = RedisSettings()
    api: ApiSettings = ApiSettings()
    livekit: LiveKitSettings = LiveKitSettings()
    auth: AuthSettings = AuthSettings()
    aggregation_timeout_ms: float = 500.0
    pipeline_queue_size: int = 10
    frame_store_ttl_sec: float = 5.0
