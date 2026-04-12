# apps/event_processor/rule_engine.py
"""
File Use:
    Pluggable rule engine for evaluating pipeline results and generating
    alerts. Supports configurable alert rules and deduplication strategies.

Implements:
    - AlertRule (abstract base for alert rules)
    - ThresholdAlertRule (simple confidence-threshold rule)
    - CooldownSuppressor (time-based deduplication)
    - RuleEngine (evaluates rules against FrameResults)

Depends On:
    - shared.contracts.frame_result
    - shared.contracts.alert_event
    - shared.contracts.pipeline_result
    - shared.utils.logging

Used By:
    - apps/event_processor/main.py
"""

import time
from abc import ABC, abstractmethod
from typing import Any

from shared.contracts.alert_event import AlertEvent, AlertSeverity
from shared.contracts.frame_result import FrameResult
from shared.contracts.pipeline_result import Detection
from shared.utils.logging import get_logger


class AlertRule(ABC):
    """Abstract base class for alert rules.

    Responsibilities:
        - Evaluate pipeline results and decide if an alert should be generated.
        - Provide a pluggable interface for different alert strategies.
    """

    @abstractmethod
    def evaluate(self, frame_result: FrameResult) -> list[AlertEvent]:
        """Evaluate the frame result and return any generated alerts.

        Args:
            frame_result: Aggregated results from all pipelines for one frame.

        Returns:
            List of AlertEvents to raise (may be empty).
        """


class ThresholdAlertRule(AlertRule):
    """Simple rule: trigger an alert when a specific label is detected
    above a confidence threshold.

    Responsibilities:
        - Match detections by pipeline_id and label.
        - Filter by confidence threshold.
        - Generate AlertEvents with configurable type and severity.
    """

    def __init__(
        self,
        pipeline_id: str,
        label: str,
        confidence_threshold: float = 0.6,
        alert_type: str | None = None,
        severity: AlertSeverity = AlertSeverity.MEDIUM,
    ) -> None:
        """Initialize the threshold rule.

        Args:
            pipeline_id: Pipeline whose detections to evaluate.
            label: Detection label to match.
            confidence_threshold: Minimum confidence to trigger alert.
            alert_type: Alert type string (defaults to label if not set).
            severity: Severity level for generated alerts.
        """
        self._pipeline_id = pipeline_id
        self._label = label
        self._confidence_threshold = confidence_threshold
        self._alert_type = alert_type or label
        self._severity = severity

    def evaluate(self, frame_result: FrameResult) -> list[AlertEvent]:
        """Check if the target pipeline produced matching detections.

        Args:
            frame_result: Aggregated results for one frame.

        Returns:
            List of AlertEvents for each matching detection above threshold.
        """
        pipeline_result = frame_result.results.get(self._pipeline_id)
        if pipeline_result is None:
            return []

        alerts: list[AlertEvent] = []
        for detection in pipeline_result.detections:
            if (
                detection.label == self._label
                and detection.confidence >= self._confidence_threshold
            ):
                alerts.append(
                    AlertEvent(
                        tenant_id=frame_result.tenant_id,
                        camera_id=frame_result.camera_id,
                        frame_id=frame_result.frame_id,
                        alert_type=self._alert_type,
                        timestamp=frame_result.timestamp,
                        severity=self._severity,
                        confidence=detection.confidence,
                        pipeline_id=self._pipeline_id,
                        details={"bbox": detection.bbox, "label": detection.label},
                    )
                )
        return alerts


class SustainedDetectionRule(AlertRule):
    """Rule that only fires after N consecutive frames of detection.

    Reduces false positives by requiring persistent detection across
    multiple frames before triggering an alert. Counter resets after
    firing so re-triggering requires another N-frame burst. Counter
    also resets if a frame arrives without the expected detection.
    """

    def __init__(
        self,
        pipeline_id: str,
        label: str,
        confidence_threshold: float = 0.6,
        required_consecutive_frames: int = 5,
        alert_type: str | None = None,
        severity: AlertSeverity = AlertSeverity.MEDIUM,
    ) -> None:
        self._pipeline_id = pipeline_id
        self._label = label
        self._confidence_threshold = confidence_threshold
        self._required = required_consecutive_frames
        self._alert_type = alert_type or label
        self._severity = severity
        # Track consecutive detection counts per (camera_id,)
        self._consecutive_counts: dict[str, int] = {}
        # Store the latest matching detection per camera for alert details
        self._last_detection: dict[str, Detection] = {}

    def evaluate(self, frame_result: FrameResult) -> list[AlertEvent]:
        camera_id = frame_result.camera_id
        pipeline_result = frame_result.results.get(self._pipeline_id)

        # Check if matching detection exists in this frame
        matched_detection: Detection | None = None
        if pipeline_result is not None:
            for det in pipeline_result.detections:
                if (
                    det.label == self._label
                    and det.confidence >= self._confidence_threshold
                ):
                    matched_detection = det
                    break  # take first matching detection

        if matched_detection is None:
            # No detection: reset counter
            self._consecutive_counts[camera_id] = 0
            return []

        # Increment counter
        count = self._consecutive_counts.get(camera_id, 0) + 1
        self._consecutive_counts[camera_id] = count
        self._last_detection[camera_id] = matched_detection

        if count >= self._required:
            # Fire alert and reset counter
            self._consecutive_counts[camera_id] = 0
            return [
                AlertEvent(
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
                    },
                )
            ]

        return []


class CooldownSuppressor:
    """Time-based alert deduplication.

    Responsibilities:
        - Suppress duplicate alerts of the same type for the same camera
          within a cooldown window.
        - Track suppression metrics.
    """

    def __init__(self, cooldown_sec: float = 30.0) -> None:
        """Initialize the suppressor.

        Args:
            cooldown_sec: Seconds to suppress duplicate alerts after the first.
        """
        self._cooldown_sec = cooldown_sec
        # Key: (tenant_id, camera_id, alert_type) → last alert timestamp
        self._last_alert: dict[tuple[str, str, str], float] = {}
        self._suppressed_count: int = 0

    @property
    def suppressed_count(self) -> int:
        """Total number of alerts suppressed."""
        return self._suppressed_count

    def should_suppress(self, alert: AlertEvent) -> bool:
        """Check if this alert should be suppressed.

        Args:
            alert: The alert to check.

        Returns:
            True if the alert should be suppressed, False if it should pass.
        """
        key = (alert.tenant_id, alert.camera_id, alert.alert_type)
        now = time.time()
        last = self._last_alert.get(key, 0.0)

        if now - last < self._cooldown_sec:
            self._suppressed_count += 1
            return True

        self._last_alert[key] = now
        return False


class RuleEngine:
    """Evaluates rules against FrameResults and produces deduplicated alerts.

    Responsibilities:
        - Run all registered rules against each FrameResult.
        - Apply deduplication via the suppressor.
        - Track alert generation metrics.
    """

    def __init__(
        self,
        rules: list[AlertRule] | None = None,
        suppressor: CooldownSuppressor | None = None,
    ) -> None:
        """Initialize the rule engine.

        Args:
            rules: List of alert rules to evaluate.
            suppressor: Optional deduplication suppressor.
        """
        self._rules: list[AlertRule] = rules or []
        self._suppressor = suppressor
        self._total_alerts: int = 0
        self._logger = get_logger("rule_engine")

    @property
    def total_alerts(self) -> int:
        """Total number of alerts generated (after suppression)."""
        return self._total_alerts

    def add_rule(self, rule: AlertRule) -> None:
        """Register a new alert rule.

        Args:
            rule: The rule to add.
        """
        self._rules.append(rule)

    def process(self, frame_result: FrameResult) -> list[AlertEvent]:
        """Evaluate all rules against a FrameResult.

        Args:
            frame_result: Aggregated pipeline results for one frame.

        Returns:
            List of non-suppressed AlertEvents.
        """
        all_alerts: list[AlertEvent] = []

        # 1) Evaluate all rules
        for rule in self._rules:
            try:
                alerts = rule.evaluate(frame_result)
                all_alerts.extend(alerts)
            except Exception as e:
                self._logger.error("rule_evaluation_error", error=str(e))

        # 2) Apply deduplication
        if self._suppressor is not None:
            all_alerts = [a for a in all_alerts if not self._suppressor.should_suppress(a)]

        self._total_alerts += len(all_alerts)
        return all_alerts
