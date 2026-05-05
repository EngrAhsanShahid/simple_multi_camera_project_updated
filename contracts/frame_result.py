# shared/contracts/frame_result.py
"""
File Use:
    Defines the aggregated frame result contract. The Event Processor
    merges PipelineResults from all pipelines into a single FrameResult
    before passing to the Rule Engine.

Implements:
    - FrameResult (aggregated pipeline outputs for one frame)

Depends On:
    - shared.contracts.pipeline_result

Used By:
    - apps/event_processor/aggregator.py
    - apps/event_processor/rule_engine.py
"""

from pydantic import BaseModel

from contracts.pipeline_result import PipelineResult


class FrameResult(BaseModel):
    """Aggregated results from all pipelines for a single frame.

    Responsibilities:
        - Namespace each pipeline's output under its pipeline_id.
        - Track completeness (all expected pipelines reported vs timeout).
    """

    tenant_id: str
    camera_id: str
    frame_id: str
    timestamp: float
    results: dict[str, PipelineResult]  # keyed by pipeline_id
    expected_pipelines: int
    received_pipelines: int
    is_complete: bool  # True if all expected pipelines reported
