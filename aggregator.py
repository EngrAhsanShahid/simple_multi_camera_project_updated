import time
from dataclasses import dataclass, field

from shared.contracts import FrameResult, PipelineResult


@dataclass
class PendingFrame:
    tenant_id: str
    camera_id: str
    frame_id: str
    timestamp: float
    expected_pipelines: int
    results: dict[str, PipelineResult] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


class FrameAggregator:
    def __init__(self, timeout_ms: float = 500.0) -> None:
        self.timeout_sec = timeout_ms / 1000.0
        self.pending: dict[str, PendingFrame] = {}

    def add_result(self, pipeline_result: PipelineResult, expected_pipelines: int) -> FrameResult | None:
        frame_id = pipeline_result.frame_id

        if frame_id not in self.pending:
            self.pending[frame_id] = PendingFrame(
                tenant_id=pipeline_result.tenant_id,
                camera_id=pipeline_result.camera_id,
                frame_id=frame_id,
                timestamp=pipeline_result.timestamp,
                expected_pipelines=expected_pipelines,
            )

        pending = self.pending[frame_id]
        pending.results[pipeline_result.pipeline_id] = pipeline_result
####
        if len(pending.results) >= pending.expected_pipelines:
            del self.pending[frame_id]
            return FrameResult(
                tenant_id=pending.tenant_id,
                camera_id=pending.camera_id,
                frame_id=pending.frame_id,
                timestamp=pending.timestamp,
                results=dict(pending.results),
                expected_pipelines=pending.expected_pipelines,
                received_pipelines=len(pending.results),
                is_complete=True,
            )
        return None

    def check_timeouts(self) -> list[FrameResult]:
        now = time.monotonic()
        expired: list[str] = []
        outputs: list[FrameResult] = []

        for frame_id, pending in list(self.pending.items()):
            if now - pending.created_at >= self.timeout_sec:
                expired.append(frame_id)

        for frame_id in expired:
            pending = self.pending.pop(frame_id)
            outputs.append(
                FrameResult(
                    tenant_id=pending.tenant_id,
                    camera_id=pending.camera_id,
                    frame_id=pending.frame_id,
                    timestamp=pending.timestamp,
                    results=dict(pending.results),
                    expected_pipelines=pending.expected_pipelines,
                    received_pipelines=len(pending.results),
                    is_complete=False,
                )
            )
        return outputs
