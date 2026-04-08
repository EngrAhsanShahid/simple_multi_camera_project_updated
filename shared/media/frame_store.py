# shared/media/frame_store.py
"""
File Use:
    Thread-safe in-memory store for original frames, keyed by frame_id.
    Frames are stored when dispatched by the IngestRouter and retrieved
    by the EventProcessor for annotation and snapshot capture. Expired
    frames are automatically evicted based on a configurable TTL.

Implements:
    - FrameStore (TTL-based frame storage)

Depends On:
    - numpy
    - threading

Used By:
    - apps/ingest_router/frame_dispatcher.py (stores frames)
    - apps/event_processor/main.py (retrieves frames for annotation)
    - run.py (retrieves frames for alert snapshots)
"""

import threading
import time

import numpy as np

from shared.utils.logging import get_logger

logger = get_logger("frame_store")


class FrameStore:
    """Thread-safe store for original frames with TTL-based eviction.

    Responsibilities:
        - Store original frames keyed by frame_id for later retrieval.
        - Automatically evict frames older than the configured TTL.
        - Track eviction metrics for monitoring.
    """

    def __init__(self, ttl_sec: float = 5.0) -> None:
        """Initialize the frame store.

        Args:
            ttl_sec: Time-to-live in seconds. Frames older than this are evicted.
        """
        self._ttl_sec = ttl_sec
        # frame_id -> (numpy_frame, camera_id, store_timestamp)
        self._frames: dict[str, tuple[np.ndarray, str, float]] = {}
        self._lock = threading.Lock()
        self._evicted_count: int = 0
        self._running = False
        self._cleanup_thread: threading.Thread | None = None

    @property
    def size(self) -> int:
        """Current number of stored frames."""
        with self._lock:
            return len(self._frames)

    @property
    def evicted_count(self) -> int:
        """Total number of frames evicted due to TTL expiration."""
        return self._evicted_count

    @property
    def ttl_sec(self) -> float:
        """Configured TTL in seconds."""
        return self._ttl_sec

    def start(self) -> None:
        """Start the background cleanup thread."""
        if self._running:
            return
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="frame-store-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    def stop(self) -> None:
        """Stop the background cleanup thread."""
        self._running = False
        if self._cleanup_thread is not None:
            self._cleanup_thread.join(timeout=3.0)
            self._cleanup_thread = None

    def store(self, frame_id: str, camera_id: str, frame: np.ndarray) -> None:
        """Store a frame for later retrieval.

        Args:
            frame_id: Unique frame identifier.
            camera_id: Camera that produced this frame (for metrics).
            frame: Original BGR frame as numpy array.
        """
        with self._lock:
            self._frames[frame_id] = (frame, camera_id, time.monotonic())

    def get(self, frame_id: str) -> np.ndarray | None:
        """Retrieve a stored frame by frame_id.

        Args:
            frame_id: The frame's unique identifier.

        Returns:
            The numpy frame or None if not found or expired.
        """
        with self._lock:
            entry = self._frames.get(frame_id)
            if entry is None:
                return None
            frame, _camera_id, _timestamp = entry
            return frame

    def remove(self, frame_id: str) -> None:
        """Remove a frame from the store (consumed successfully).

        Args:
            frame_id: The frame's unique identifier.
        """
        with self._lock:
            self._frames.pop(frame_id, None)

    def cleanup(self) -> int:
        """Evict all frames older than the TTL.

        Returns:
            Number of frames evicted.
        """
        now = time.monotonic()
        evicted = 0

        with self._lock:
            expired_ids = [
                fid
                for fid, (_frame, _cam, ts) in self._frames.items()
                if now - ts > self._ttl_sec
            ]
            for fid in expired_ids:
                _frame, camera_id, _ts = self._frames.pop(fid)
                evicted += 1
                # Lazy import to avoid circular imports at module level
                try:
                    from shared.metrics.collector import metrics_collector
                    metrics_collector.increment(camera_id, "frames_dropped_ttl")
                except ImportError:
                    pass

        self._evicted_count += evicted
        if evicted > 0:
            logger.debug("frames_evicted", count=evicted, store_size=self.size)

        return evicted

    def clear(self) -> None:
        """Remove all frames from the store."""
        with self._lock:
            self._frames.clear()

    def _cleanup_loop(self) -> None:
        """Background loop that periodically evicts expired frames."""
        while self._running:
            time.sleep(1.0)
            self.cleanup()


# Singleton instance
frame_store = FrameStore()
