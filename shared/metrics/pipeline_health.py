# shared/metrics/pipeline_health.py
"""
File Use:
    Thread-safe shared singleton for pipeline health status. Written by
    the PipelineRunner's health check loop, read by the Status API.

Implements:
    - PipelineHealthStatus (enum)
    - PipelineHealthStore (thread-safe singleton)

Depends On:
    - threading

Used By:
    - apps/pipeline_runner/main.py (writer)
    - apps/api_service/routes/status.py (reader)
"""

import threading
import time
from enum import StrEnum
from typing import Any


class PipelineHealthStatus(StrEnum):
    """Pipeline health states."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRASHED = "crashed"
    STOPPED = "stopped"


class PipelineHealthStore:
    """Thread-safe store for pipeline health data.

    Written periodically by the PipelineRunner's health check loop,
    read by the Status API endpoint.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._last_updated: float = 0.0

    def update(self, health_dict: dict[str, Any]) -> None:
        """Update the stored pipeline health data.

        Args:
            health_dict: Per-pipeline health dict from PipelineProcessManager.check_health().
        """
        enriched = {}
        for pid, info in health_dict.items():
            alive = info.get("alive", False)
            restart_count = info.get("restart_count", 0)

            if not alive:
                status = PipelineHealthStatus.CRASHED
            elif restart_count > 0:
                status = PipelineHealthStatus.DEGRADED
            else:
                status = PipelineHealthStatus.HEALTHY

            enriched[pid] = {
                **info,
                "health_status": status,
            }

        with self._lock:
            self._data = enriched
            self._last_updated = time.time()

    def get(self) -> dict[str, Any]:
        """Get the current pipeline health data."""
        with self._lock:
            return dict(self._data)

    @property
    def last_updated(self) -> float:
        """Timestamp of the last update."""
        with self._lock:
            return self._last_updated

    def reset(self) -> None:
        """Clear all data. Used in tests."""
        with self._lock:
            self._data.clear()
            self._last_updated = 0.0


# Singleton instance
pipeline_health_store = PipelineHealthStore()
