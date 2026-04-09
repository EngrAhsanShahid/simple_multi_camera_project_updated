"""Simple in-memory FrameBuffer used by the LiveKit publisher.

This is a lightweight replacement for the original project's frame
buffer. It stores the latest frame per camera_id and is safe for
concurrent access from the camera reader and the publisher.
"""
from typing import Optional
import threading
import numpy as np


class FrameBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: dict[str, np.ndarray] = {}

    def put(self, camera_id: str, frame: np.ndarray) -> None:
        """Store the latest frame for a camera."""
        with self._lock:
            self._frames[camera_id] = frame

    def get(self, camera_id: str) -> Optional[np.ndarray]:
        """Retrieve the latest frame for a camera or None."""
        with self._lock:
            return self._frames.get(camera_id)

    @property
    def camera_ids(self) -> list[str]:
        with self._lock:
            return list(self._frames.keys())


# Global frame buffer instance used across the app
frame_buffer = FrameBuffer()
