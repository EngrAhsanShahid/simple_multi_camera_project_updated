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
    - shared/storage/mongo_client.py
    - shared/storage/minio_client.py
    - apps/api_service/main.py
    - apps/event_processor/main.py
    - apps/pipeline_runner/main.py
    - apps/media_service/livekit_publisher.py
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
    api_secret: str = "a]3Kf9#pLm2$wQz8vR5xN7bY0cJ4hT6e"
    publish_fps: int = 15
    publish_width: int = 640
    publish_height: int = 480


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

    # Alert evidence settings
    alert_clip_enabled: bool = False
    alert_clip_pre_sec: float = 5.0
    alert_clip_post_sec: float = 5.0
    alert_clip_fps: float = 5.0
    alert_snapshot_enabled: bool = True

    mongo: MongoSettings = MongoSettings()
    minio: MinioSettings = MinioSettings()
    api: ApiSettings = ApiSettings()
    livekit: LiveKitSettings = LiveKitSettings()
    auth: AuthSettings = AuthSettings()
    aggregation_timeout_ms: float = 500.0
    pipeline_queue_size: int = 10
    frame_store_ttl_sec: float = 5.0
