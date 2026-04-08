# shared/contracts/__init__.py
"""
File Use:
    Public API for all shared message contracts. Import contracts from
    this package rather than from individual modules.

Implements:
    - Re-exports of all contract models

Depends On:
    - shared.contracts.camera_config
    - shared.contracts.frame_packet
    - shared.contracts.pipeline_result
    - shared.contracts.frame_result
    - shared.contracts.alert_event

Used By:
    - apps/*
    - pipelines/*
    - tests/*
"""

from shared.contracts.camera_config import CameraConfig, SourceType
from shared.contracts.frame_packet import FramePacket
from shared.contracts.pipeline_result import Detection, PipelineResult
from shared.contracts.frame_result import FrameResult
from shared.contracts.alert_event import AlertEvent, AlertSeverity, AlertStatus
from shared.contracts.user import User, UserRole, PLATFORM_ROLES, ORG_ROLES
from shared.contracts.tenant import Tenant

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
    "User",
    "UserRole",
    "PLATFORM_ROLES",
    "ORG_ROLES",
    "Tenant",
]
