# shared/contracts/pipeline_result.py
"""
File Use:
    Defines the output contract for AI pipelines. Each pipeline produces
    a PipelineResult containing zero or more Detection objects.

Implements:
    - Detection (single detected object/event)
    - PipelineResult (complete output from one pipeline for one frame)

Depends On:
    - pydantic

Used By:
    - pipelines/base_pipeline.py
    - apps/event_processor/aggregator.py
    - apps/event_processor/rule_engine.py
"""

from pydantic import BaseModel


class Detection(BaseModel):
    """A single detection produced by a pipeline.

    Responsibilities:
        - Represent one detected object or event with location and confidence.
        - Carry optional pipeline-specific metadata (tracking_id, keypoints, etc.).
    """

    label: str
    bbox: list[float]  # [x, y, width, height]
    confidence: float
    metadata: dict | None = None


class PipelineResult(BaseModel):
    """Output from a single AI pipeline for a single frame.

    Responsibilities:
        - Carry all detections produced by one pipeline for one frame.
        - Track inference performance via inference_time_ms.
    """

    tenant_id: str
    camera_id: str
    frame_id: str
    pipeline_id: str
    timestamp: float
    detections: list[Detection] = []
    inference_time_ms: float | None = None
