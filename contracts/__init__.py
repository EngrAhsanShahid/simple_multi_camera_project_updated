# shared/contracts/__init__.py
"""Public API for shared message contracts used across the worker."""

from contracts.camera_config import CameraConfig, SourceType
from contracts.frame_packet import FramePacket
from contracts.pipeline_result import Detection, PipelineResult
from contracts.frame_result import FrameResult
from contracts.alert_event import AlertEvent, AlertSeverity, AlertStatus

__all__ = [
    "CameraConfig",
    "SourceType",
    "FramePacket",
    "PipelineResult",
    "Detection",
    "FrameResult",
    "AlertEvent",
    "AlertSeverity",
    "AlertStatus",
]
