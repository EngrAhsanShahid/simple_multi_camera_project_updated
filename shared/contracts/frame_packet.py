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
    - apps/ingest_router/frame_dispatcher.py
    - apps/pipeline_runner/runner.py
    - pipelines/base_pipeline.py
"""

from uuid import uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class FramePacket(BaseModel):
    """A single frame selected for AI processing.

    Responsibilities:
        - Carry decoded frame data and metadata between services.
        - Provide immutable frame identity via tenant_id + camera_id + frame_id.
        - Specify which pipelines should process this frame.

    In MVP, passed through multiprocessing.Queue (pickle serialization).
    The frame_ref abstraction allows future migration to shared memory.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tenant_id: str
    camera_id: str
    frame_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: float
    width: int
    height: int
    pipelines: list[str]
    frame: np.ndarray

    @property
    def pipeline_count(self) -> int:
        """Return the number of pipelines expected to process this frame."""
        return len(self.pipelines)
