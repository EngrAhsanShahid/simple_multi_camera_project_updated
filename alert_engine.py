from shared.contracts import AlertEvent, AlertSeverity, FrameResult


class AlertEngine:
    def build_alerts(self, frame_result: FrameResult) -> list[AlertEvent]:
        alerts: list[AlertEvent] = []

        for pipeline_id, pipeline_result in frame_result.results.items():
            for detection in pipeline_result.detections:
                alerts.append(
                    AlertEvent(
                        tenant_id=frame_result.tenant_id,
                        camera_id=frame_result.camera_id,
                        frame_id=frame_result.frame_id,
                        alert_type=detection.label,
                        timestamp=frame_result.timestamp,
                        severity=AlertSeverity.HIGH,
                        confidence=detection.confidence,
                        pipeline_id=pipeline_id,
                        details={
                            "bbox": detection.bbox,
                            "metadata": detection.metadata,
                        },
                    )
                )
        return alerts
