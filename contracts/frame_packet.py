# shared/contracts/frame_packet.py
"""
File Use:
    Defines the FramePacket contract — the primary data unit flowing
    from the Ingest Router to the Pipeline Runner. Carries a decoded
    video frame alongside its metadata.

Implements:
    - FramePacket (pydantic model with numpy frame)

Depends On:
    - numpy
    - pydantic

Used By:
    - main.py (camera_reader_task)
    - pipeline_manager.py
    - pipelines/ppe/pipeline.py
"""

from uuid import uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator


class FramePacket(BaseModel):
    """A single frame selected for AI processing.

    Responsibilities:
        - Carry decoded frame data and metadata between services.
        - Provide immutable frame identity via tenant_id + camera_id + frame_id.
        - Specify which pipelines should process this frame.

    frame_id format: {tenant_id}_{camera_id}_{12-char hex} — auto-generated
    so logs/storage paths are traceable to their origin without joining tables.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tenant_id: str
    camera_id: str
    frame_id: str = ""
    timestamp: float
    width: int
    height: int
    pipelines: list[str]
    frame: np.ndarray

    @model_validator(mode="after")
    def _generate_frame_id(self) -> "FramePacket":
        if not self.frame_id:
            self.frame_id = f"{self.tenant_id}_{self.camera_id}_{uuid4().hex[:12]}"
        return self

    @property
    def pipeline_count(self) -> int:
        """Return the number of pipelines expected to process this frame."""
        return len(self.pipelines)
