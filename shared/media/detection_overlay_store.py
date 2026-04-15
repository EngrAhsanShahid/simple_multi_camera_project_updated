"""Shared store for the latest detections per camera.

This uses Redis so the detection processor and the LiveKit publisher can
run in different processes while still sharing the most recent overlay data.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import redis.asyncio as redis

from shared.contracts.pipeline_result import Detection


class DetectionOverlayStore:
    def __init__(self, redis_url: str = "redis://192.168.100.4:6379", ttl_seconds: int = 10) -> None:
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._redis: redis.Redis | None = None
        self._last_warning_ts: float = 0.0
        self._lock = threading.Lock()
        self._local_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)

        try:
            await self._redis.ping()
        except Exception:
            self._redis = None
            now = time.monotonic()
            if now - self._last_warning_ts > 30.0:
                self._last_warning_ts = now
                print(f"[WARNING] detection overlay Redis unavailable at {self._redis_url}; live stream will continue without overlays")
            raise
        return self._redis

    @staticmethod
    def _key(camera_id: str) -> str:
        return f"livekit:overlay:{camera_id}"

    def _put_local(self, camera_id: str, payload: list[dict[str, Any]]) -> None:
        with self._lock:
            self._local_cache[camera_id] = (payload, time.monotonic())

    def _get_local(self, camera_id: str) -> list[dict[str, Any]]:
        with self._lock:
            entry = self._local_cache.get(camera_id)
            if entry is None:
                return []
            payload, stored_at = entry
            if time.monotonic() - stored_at > self._ttl_seconds:
                self._local_cache.pop(camera_id, None)
                return []
            return list(payload)

    async def put(self, camera_id: str, detections: list[Detection]) -> None:
        """Store the latest detections for a camera with a short TTL."""
        payload: list[dict[str, Any]] = [detection.model_dump() for detection in detections]
        self._put_local(camera_id, payload)

        try:
            client = await self._get_redis()
        except Exception:
            return

        try:
            await client.set(self._key(camera_id), json.dumps(payload), ex=self._ttl_seconds)
        except Exception:
            return

    async def get(self, camera_id: str) -> list[Detection]:
        """Return the latest detections for a camera, or an empty list."""
        payload = self._get_local(camera_id)
        if payload:
            detections: list[Detection] = []
            for item in payload:
                try:
                    detections.append(Detection.model_validate(item))
                except Exception:
                    continue
            if detections:
                return detections

        return await self.get_remote(camera_id)

    async def get_remote(self, camera_id: str) -> list[Detection]:
        """Return the latest detections from Redis only.

        This is used by the LiveKit publisher so the stream is driven by the
        shared Redis data rather than the local fallback cache.
        """
        try:
            client = await self._get_redis()
        except Exception:
            return []

        try:
            raw_value = await client.get(self._key(camera_id))
        except Exception:
            return []
        if not raw_value:
            return []

        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            return []

        detections: list[Detection] = []
        for item in payload:
            try:
                detections.append(Detection.model_validate(item))
            except Exception:
                continue
        return detections

    async def clear(self, camera_id: str | None = None) -> None:
        """Clear one camera or all stored detections."""
        with self._lock:
            if camera_id is None:
                self._local_cache.clear()
            else:
                self._local_cache.pop(camera_id, None)

        try:
            client = await self._get_redis()
        except Exception:
            return

        if camera_id is None:
            try:
                keys = await client.keys("livekit:overlay:*")
                if keys:
                    await client.delete(*keys)
            except Exception:
                return
            return
        try:
            await client.delete(self._key(camera_id))
        except Exception:
            return


detection_overlay_store = DetectionOverlayStore()