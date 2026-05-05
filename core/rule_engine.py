# apps/event_processor/rule_engine.py
"""
File Use:
    Pluggable rule engine for evaluating pipeline results and generating
    alerts. Supports configurable alert rules with per-rule cooldown suppression.

Implements:
    - AlertRule (abstract base for alert rules)
    - ThresholdAlertRule (fires on confidence threshold per frame)
    - SustainedDetectionRule (fires after N consecutive frames)
    - WindowDetectionRule (fires when X out of last N frames had detection)
    - CooldownSuppressor (per-rule time-based deduplication)
    - RuleEngine (evaluates rules against FrameResults)

Depends On:
    - shared.contracts.frame_result
    - shared.contracts.alert_event
    - shared.contracts.pipeline_result
    - shared.utils.logging

Used By:
    - main.py
"""

import time
from abc import ABC, abstractmethod
from collections import deque

from contracts.alert_event import AlertEvent, AlertSeverity
from contracts.frame_result import FrameResult
from contracts.pipeline_result import Detection
from utils.logging import get_logger


class CooldownSuppressor:
    """Per-rule time-based alert deduplication.

    Suppresses duplicate alerts of the same type for the same camera
    within a cooldown window. Each rule owns its own instance so
    cooldown periods are independent across rules.
    """

    def __init__(self, cooldown_sec: float = 30.0) -> None:
        self._cooldown_sec = cooldown_sec
        # Key: (tenant_id, camera_id, alert_type) → last alert timestamp
        self._last_alert: dict[tuple[str, str, str], float] = {}
        self._suppressed_count: int = 0

    @property
    def suppressed_count(self) -> int:
        return self._suppressed_count

    def should_suppress(self, alert: AlertEvent) -> bool:
        key = (alert.tenant_id, alert.camera_id, alert.alert_type)
        now = time.time()
        last = self._last_alert.get(key, 0.0)

        if now - last < self._cooldown_sec:
            self._suppressed_count += 1
            return True

        self._last_alert[key] = now
        return False


class AlertRule(ABC):
    """Abstract base class for alert rules."""

    @abstractmethod
    def evaluate(self, frame_result: FrameResult) -> list[AlertEvent]:
        """Evaluate the frame result and return any generated alerts."""


class ThresholdAlertRule(AlertRule):
    """Fires an alert when a label is detected above a confidence threshold.

    Cooldown is per-rule — two threshold rules for different labels have
    independent cooldown windows.
    """

    def __init__(
        self,
        pipeline_id: str,
        label: str,
        confidence_threshold: float = 0.6,
        alert_type: str | None = None,
        severity: AlertSeverity = AlertSeverity.MEDIUM,
        cooldown_sec: float = 30.0,
        draw: bool = True,
    ) -> None:
        self._pipeline_id = pipeline_id
        self._label = label
        self._confidence_threshold = confidence_threshold
        self._alert_type = alert_type or label
        self._severity = severity
        self._draw = draw
        self._suppressor = CooldownSuppressor(cooldown_sec=cooldown_sec)

    def evaluate(self, frame_result: FrameResult) -> list[AlertEvent]:
        pipeline_result = frame_result.results.get(self._pipeline_id)
        if pipeline_result is None:
            return []

        alerts: list[AlertEvent] = []
        for detection in pipeline_result.detections:
            if (
                detection.label == self._label
                and detection.confidence >= self._confidence_threshold
            ):
                alert = AlertEvent(
                    tenant_id=frame_result.tenant_id,
                    camera_id=frame_result.camera_id,
                    frame_id=frame_result.frame_id,
                    alert_type=self._alert_type,
                    timestamp=frame_result.timestamp,
                    severity=self._severity,
                    confidence=detection.confidence,
                    pipeline_id=self._pipeline_id,
                    details={"bbox": detection.bbox, "label": detection.label, "draw": self._draw},
                )
                if not self._suppressor.should_suppress(alert):
                    alerts.append(alert)
        return alerts


class SustainedDetectionRule(AlertRule):
    """Fires after a label appears in N strictly consecutive frames.

    Counter resets on any frame without the detection, and resets again
    after firing — requiring another full N-frame burst to re-trigger.
    """

    def __init__(
        self,
        pipeline_id: str,
        label: str,
        confidence_threshold: float = 0.6,
        required_consecutive_frames: int = 5,
        alert_type: str | None = None,
        severity: AlertSeverity = AlertSeverity.MEDIUM,
        cooldown_sec: float = 30.0,
        draw: bool = True,
    ) -> None:
        self._pipeline_id = pipeline_id
        self._label = label
        self._confidence_threshold = confidence_threshold
        self._required = required_consecutive_frames
        self._alert_type = alert_type or label
        self._severity = severity
        self._draw = draw
        self._suppressor = CooldownSuppressor(cooldown_sec=cooldown_sec)
        self._consecutive_counts: dict[str, int] = {}
        self._last_detection: dict[str, Detection] = {}

    def evaluate(self, frame_result: FrameResult) -> list[AlertEvent]:
        camera_id = frame_result.camera_id
        pipeline_result = frame_result.results.get(self._pipeline_id)

        matched_detection: Detection | None = None
        if pipeline_result is not None:
            for det in pipeline_result.detections:
                if det.label == self._label and det.confidence >= self._confidence_threshold:
                    matched_detection = det
                    break

        if matched_detection is None:
            self._consecutive_counts[camera_id] = 0
            return []

        count = self._consecutive_counts.get(camera_id, 0) + 1
        self._consecutive_counts[camera_id] = count
        self._last_detection[camera_id] = matched_detection

        if count >= self._required:
            self._consecutive_counts[camera_id] = 0
            alert = AlertEvent(
                tenant_id=frame_result.tenant_id,
                camera_id=camera_id,
                frame_id=frame_result.frame_id,
                alert_type=self._alert_type,
                timestamp=frame_result.timestamp,
                severity=self._severity,
                confidence=matched_detection.confidence,
                pipeline_id=self._pipeline_id,
                details={
                    "bbox": matched_detection.bbox,
                    "label": matched_detection.label,
                    "consecutive_frames": self._required,
                    "draw": self._draw,
                },
            )
            if not self._suppressor.should_suppress(alert):
                return [alert]

        return []


class WindowDetectionRule(AlertRule):
    """Fires when a label appears in at least min_detections of the last window_size frames.

    More robust than SustainedDetectionRule for noisy streams — a single missed
    frame does not reset the counter. Uses a sliding window (deque) per camera.

    Example: window_size=5, min_detections=3 fires if 3+ of the last 5 frames
    had the detection, regardless of which 3.
    """

    def __init__(
        self,
        pipeline_id: str,
        label: str,
        confidence_threshold: float = 0.6,
        window_size: int = 5,
        min_detections: int = 3,
        alert_type: str | None = None,
        severity: AlertSeverity = AlertSeverity.MEDIUM,
        cooldown_sec: float = 30.0,
        draw: bool = True,
    ) -> None:
        self._pipeline_id = pipeline_id
        self._label = label
        self._confidence_threshold = confidence_threshold
        self._window_size = window_size
        self._min_detections = min_detections
        self._alert_type = alert_type or label
        self._severity = severity
        self._draw = draw
        self._suppressor = CooldownSuppressor(cooldown_sec=cooldown_sec)
        # deque per camera_id — each entry is True if detection was present that frame
        self._windows: dict[str, deque[bool]] = {}
        self._last_detection: dict[str, Detection] = {}

    def evaluate(self, frame_result: FrameResult) -> list[AlertEvent]:
        camera_id = frame_result.camera_id

        if camera_id not in self._windows:
            self._windows[camera_id] = deque(maxlen=self._window_size)

        window = self._windows[camera_id]
        pipeline_result = frame_result.results.get(self._pipeline_id)

        matched_detection: Detection | None = None
        if pipeline_result is not None:
            for det in pipeline_result.detections:
                if det.label == self._label and det.confidence >= self._confidence_threshold:
                    matched_detection = det
                    break

        window.append(matched_detection is not None)

        if matched_detection is not None:
            self._last_detection[camera_id] = matched_detection

        if sum(window) >= self._min_detections:
            last_det = self._last_detection.get(camera_id)
            if last_det is None:
                return []

            alert = AlertEvent(
                tenant_id=frame_result.tenant_id,
                camera_id=camera_id,
                frame_id=frame_result.frame_id,
                alert_type=self._alert_type,
                timestamp=frame_result.timestamp,
                severity=self._severity,
                confidence=last_det.confidence,
                pipeline_id=self._pipeline_id,
                details={
                    "bbox": last_det.bbox,
                    "label": last_det.label,
                    "detections_in_window": int(sum(window)),
                    "window_size": self._window_size,
                    "draw": self._draw,
                },
            )
            if not self._suppressor.should_suppress(alert):
                return [alert]

        return []


class RuleEngine:
    """Evaluates rules against FrameResults and produces alerts.

    Suppression is handled per-rule — each rule owns its own CooldownSuppressor
    so cooldown periods are independent across rules.
    """

    def __init__(self, rules: list[AlertRule] | None = None) -> None:
        self._rules: list[AlertRule] = rules or []
        self._total_alerts: int = 0
        self._logger = get_logger("rule_engine")

    @property
    def total_alerts(self) -> int:
        return self._total_alerts

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    def process(self, frame_result: FrameResult) -> list[AlertEvent]:
        """Evaluate all rules against a FrameResult.

        Each rule handles its own cooldown suppression internally.
        """
        all_alerts: list[AlertEvent] = []

        for rule in self._rules:
            try:
                alerts = rule.evaluate(frame_result)
                all_alerts.extend(alerts)
            except Exception as e:
                self._logger.error("rule_evaluation_error", error=str(e))

        self._total_alerts += len(all_alerts)
        return all_alerts
