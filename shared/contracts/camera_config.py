# shared/contracts/camera_config.py
"""
File Use:
    Defines the camera configuration contract used across the platform.
    Supports both RTSP streams and video file sources with validation.

Implements:
    - SourceType (enum for input source types)
    - CameraConfig (pydantic model for camera configuration)

Depends On:
    - pydantic

Used By:
    - apps/ingest_router/source_reader.py
    - apps/api_service/routes/cameras.py
"""

from enum import StrEnum

from pydantic import BaseModel, model_validator


class SourceType(StrEnum):
    """Input source type for a camera feed."""

    RTSP = "rtsp"
    FILE = "file"


class CameraConfig(BaseModel):
    """Configuration for a single camera source.

    Responsibilities:
        - Define camera identity and tenant ownership.
        - Specify input source (RTSP or file) with validation.
        - Control frame sampling rate and pipeline routing.
    """

    tenant_id: str
    camera_id: str
    source_path: str | None = None
    source_type: SourceType | None = None
    rtsp_url: str | None = None
    file_path: str | None = None
    loop: bool = False
    target_fps: float = 5.0
    pipelines: list[str]
    enabled: bool = True
    record_full_video: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> "CameraConfig":
        """Ensure the correct URL/path is provided for the source type."""
        if self.source_path:
            if self.source_path.startswith("rtsp://"):
                self.source_type = SourceType.RTSP
                self.rtsp_url = self.source_path
            else:
                self.source_type = SourceType.FILE
                self.file_path = self.source_path

        if self.source_type is None:
            raise ValueError("source_type is required")

        if self.source_type == SourceType.RTSP and not self.rtsp_url:
            raise ValueError("rtsp_url is required when source_type is 'rtsp'")
        if self.source_type == SourceType.FILE and not self.file_path:
            raise ValueError("file_path is required when source_type is 'file'")
        return self
