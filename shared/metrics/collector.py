# shared/metrics/collector.py
"""
File Use:
    Thread-safe per-camera metrics collector. Tracks frame counts,
    pipeline inference times, and alert counts. Resets on restart
    (no persistence). Exposed via the Status API endpoint.

Implements:
    - MetricsCollector (per-camera stats singleton)

Depends On:
    - threading

Used By:
    - apps/ingest_router/frame_dispatcher.py (frames_dispatched)
    - apps/event_processor/main.py (frames_annotated, inference times, alerts)
    - shared/media/frame_store.py (frames_dropped_ttl)
    - apps/api_service/routes/status.py (read metrics)
"""

import collections
import threading
import time
from enum import StrEnum
from typing import Any


class ConnectionStatus(StrEnum):
    """Camera connection status."""
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class MetricsCollector:
    """Thread-safe per-camera metrics collector.

    Responsibilities:
        - Track per-camera frame counters (dispatched, dropped, annotated).
        - Track per-camera per-pipeline inference time rolling averages.
        - Track global metrics (uptime, frame store stats).
        - Provide stats for the Status API.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time: float = time.monotonic()

        # Per-camera counters: camera_id -> {metric_name: count}
        self._counters: dict[str, dict[str, int]] = {}

        # Per-camera per-pipeline inference times: camera_id -> pipeline_id -> deque of ms values
        self._inference_times: dict[str, dict[str, collections.deque]] = {}

        # Rolling window size for inference time averages
        self._inference_window: int = 100

        # Camera connection health: camera_id -> {status, last_connected, last_disconnected, disconnect_count}
        self._connection: dict[str, dict[str, Any]] = {}
        self._health_alert_threshold: float = 30.0  # seconds

    def increment(self, camera_id: str, metric: str, count: int = 1) -> None:
        """Increment a per-camera counter.

        Args:
            camera_id: Camera identifier.
            metric: Metric name (e.g., "frames_dispatched", "frames_dropped_ttl").
            count: Amount to increment by.
        """
        with self._lock:
            if camera_id not in self._counters:
                self._counters[camera_id] = {}
            cam = self._counters[camera_id]
            cam[metric] = cam.get(metric, 0) + count

    def record_inference(self, camera_id: str, pipeline_id: str, time_ms: float) -> None:
        """Record a pipeline inference time for a camera.

        Args:
            camera_id: Camera identifier.
            pipeline_id: Pipeline that produced the result.
            time_ms: Inference time in milliseconds.
        """
        with self._lock:
            if camera_id not in self._inference_times:
                self._inference_times[camera_id] = {}
            cam_pipelines = self._inference_times[camera_id]
            if pipeline_id not in cam_pipelines:
                cam_pipelines[pipeline_id] = collections.deque(maxlen=self._inference_window)
            cam_pipelines[pipeline_id].append(time_ms)

    def record_connected(self, camera_id: str) -> None:
        """Record that a camera has connected or reconnected."""
        with self._lock:
            if camera_id not in self._connection:
                self._connection[camera_id] = {
                    "status": ConnectionStatus.CONNECTED,
                    "last_connected": time.time(),
                    "last_disconnected": None,
                    "disconnect_count": 0,
                }
            else:
                self._connection[camera_id]["status"] = ConnectionStatus.CONNECTED
                self._connection[camera_id]["last_connected"] = time.time()

    def record_disconnected(self, camera_id: str) -> None:
        """Record that a camera has disconnected."""
        with self._lock:
            if camera_id not in self._connection:
                self._connection[camera_id] = {
                    "status": ConnectionStatus.DISCONNECTED,
                    "last_connected": None,
                    "last_disconnected": time.time(),
                    "disconnect_count": 1,
                }
            else:
                self._connection[camera_id]["status"] = ConnectionStatus.DISCONNECTED
                self._connection[camera_id]["last_disconnected"] = time.time()
                self._connection[camera_id]["disconnect_count"] += 1

    def get_camera_health(self, camera_id: str) -> dict[str, Any]:
        """Get connection health for a camera.

        Returns:
            Dict with status, is_healthy, health_alert, disconnect_count.
        """
        with self._lock:
            conn = self._connection.get(camera_id)

        if conn is None:
            return {
                "status": ConnectionStatus.UNKNOWN,
                "is_healthy": False,
                "health_alert": False,
                "disconnect_count": 0,
            }

        status = conn["status"]
        is_healthy = status == ConnectionStatus.CONNECTED
        health_alert = False

        if status == ConnectionStatus.DISCONNECTED and conn["last_disconnected"]:
            elapsed = time.time() - conn["last_disconnected"]
            health_alert = elapsed > self._health_alert_threshold

        return {
            "status": status,
            "is_healthy": is_healthy,
            "health_alert": health_alert,
            "disconnect_count": conn["disconnect_count"],
        }

    def get_camera_stats(self, camera_id: str) -> dict[str, Any]:
        """Get stats for a specific camera.

        Args:
            camera_id: Camera identifier.

        Returns:
            Dictionary with counters and pipeline stats.
        """
        with self._lock:
            counters = dict(self._counters.get(camera_id, {}))

            pipelines = {}
            for pid, times in self._inference_times.get(camera_id, {}).items():
                if times:
                    pipelines[pid] = {
                        "avg_inference_ms": round(sum(times) / len(times), 1),
                        "count": len(times),
                    }
                else:
                    pipelines[pid] = {"avg_inference_ms": 0.0, "count": 0}

        return {
            "camera_id": camera_id,
            "frames_dispatched": counters.get("frames_dispatched", 0),
            "frames_dropped_queue": counters.get("frames_dropped_queue", 0),
            "frames_dropped_ttl": counters.get("frames_dropped_ttl", 0),
            "frames_annotated": counters.get("frames_annotated", 0),
            "alerts_generated": counters.get("alerts_generated", 0),
            "pipelines": pipelines,
            "connection": self.get_camera_health(camera_id),
        }

    def get_all_stats(self) -> dict[str, Any]:
        """Get stats for all cameras.

        Returns:
            Dictionary with global stats and per-camera stats.
        """
        with self._lock:
            camera_ids = set(self._counters.keys()) | set(self._inference_times.keys()) | set(self._connection.keys())

        cameras = {}
        for cam_id in sorted(camera_ids):
            cameras[cam_id] = self.get_camera_stats(cam_id)

        return {
            **self.get_global_stats(),
            "cameras": cameras,
        }

    def get_global_stats(self) -> dict[str, Any]:
        """Get global system metrics.

        Returns:
            Dictionary with uptime and aggregate stats.
        """
        uptime = time.monotonic() - self._start_time
        return {
            "uptime_seconds": round(uptime, 1),
        }

    def reset(self) -> None:
        """Reset all metrics. Used in tests."""
        with self._lock:
            self._counters.clear()
            self._inference_times.clear()
            self._connection.clear()
            self._start_time = time.monotonic()


# Singleton instance
metrics_collector = MetricsCollector()