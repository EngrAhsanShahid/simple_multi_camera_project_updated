# shared/messaging/queue_manager.py
"""
File Use:
    Provides a bounded multiprocessing queue with drop-oldest backpressure.
    Prevents unbounded memory growth when pipelines cannot keep up with
    the configured FPS.

Implements:
    - BoundedQueue (multiprocessing queue with backpressure)

Depends On:
    - multiprocessing
    - threading
    - collections.deque
    - shared.utils.logging

Used By:
    - apps/ingest_router/frame_dispatcher.py
    - apps/pipeline_runner/process_manager.py
"""

import multiprocessing as mp
import queue
import threading
from collections import deque
from typing import Any

from shared.utils.logging import get_logger

logger = get_logger("queue_manager")


class BoundedQueue:
    """A bounded queue with drop-oldest backpressure for frame passing.

    Responsibilities:
        - Enforce a max size, dropping the oldest item when full.
        - Track drop count for monitoring and alerting.
        - Provide a raw mp.Queue for cross-process consumption.

    Architecture:
        Internally uses a threading-safe deque for bounded storage on the
        producer side, and an mp.Queue for cross-process delivery to
        pipeline child processes. The producer thread appends to the deque
        (dropping oldest if full), and a background feeder thread moves
        items from the deque into the mp.Queue for the consumer process.

        This avoids Windows pipe-buffer deadlocks that occur when doing
        get-then-put on mp.Queue with large objects (numpy frames).
    """

    def __init__(self, maxsize: int = 10) -> None:
        self._queue: mp.Queue = mp.Queue()
        self._maxsize = maxsize
        self._buffer: deque = deque()
        self._lock = threading.Lock()
        self._drop_count: int = 0
        self._event = threading.Event()
        self._stopped = False

        # Background feeder: moves items from deque → mp.Queue
        self._feeder = threading.Thread(target=self._feed_loop, daemon=True)
        self._feeder.start()

    def _feed_loop(self) -> None:
        """Background thread that moves items from the deque to mp.Queue."""
        while not self._stopped:
            self._event.wait(timeout=0.1)
            self._event.clear()
            while True:
                with self._lock:
                    if not self._buffer:
                        break
                    item = self._buffer.popleft()
                self._queue.put(item)

    def put(self, item: Any) -> bool:
        """Put an item into the queue, dropping the oldest if full.

        Args:
            item: The item to enqueue.

        Returns:
            True if an older item was dropped to make room, False otherwise.
        """
        dropped = False
        with self._lock:
            while len(self._buffer) >= self._maxsize:
                self._buffer.popleft()
                self._drop_count += 1
                dropped = True
            self._buffer.append(item)

        self._event.set()
        return dropped

    def get(self, timeout: float | None = None) -> Any:
        """Get the next item from the queue.

        Args:
            timeout: Max seconds to wait. None blocks indefinitely.

        Returns:
            The next item in the queue.
        """
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> Any:
        """Non-blocking get. Raises queue.Empty if no items available."""
        return self._queue.get_nowait()

    def empty(self) -> bool:
        """Return True if the queue has no pending items."""
        with self._lock:
            return len(self._buffer) == 0 and self._queue.empty()

    def qsize(self) -> int:
        """Return the approximate total queue size (buffer + mp.Queue)."""
        with self._lock:
            return len(self._buffer) + self._queue.qsize()

    @property
    def drops(self) -> int:
        """Total number of dropped items since creation."""
        return self._drop_count

    def stop(self) -> None:
        """Stop the background feeder thread."""
        self._stopped = True
        self._event.set()
