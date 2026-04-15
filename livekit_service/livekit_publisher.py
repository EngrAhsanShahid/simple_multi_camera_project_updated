# apps/media_service/livekit_publisher.py
"""
File Use:
    LiveKit room publisher that reads annotated frames from FrameBuffer
    and publishes them as video tracks to per-tenant LiveKit rooms.

    Room model: one room per tenant_id. Each camera joins the tenant's
    room as a separate participant, publishing a single video track
    named after the camera_id.

Implements:
    - LiveKitPublisher

Depends On:
    - livekit (rtc SDK)
    - apps.media_service.frame_buffer
    - apps.media_service.livekit_tokens
    - shared.config.settings
    - shared.contracts.camera_config
    - shared.utils.logging

Used By:
    - run.py
"""

import asyncio
from collections import defaultdict

import cv2
import numpy as np
from livekit import rtc

from apps.media_service.frame_buffer import FrameBuffer, frame_buffer
from livekit_service.livekit_tokens import generate_publisher_token
from shared.config.settings import LiveKitSettings
from shared.contracts.camera_config import CameraConfig
from shared.media.detection_overlay_store import DetectionOverlayStore, detection_overlay_store
from shared.utils.logging import get_logger


class LiveKitPublisher:
    """Publishes annotated camera frames to per-tenant LiveKit rooms.

    Room model (Option B):
        - One LiveKit room per tenant (room name = tenant_id).
        - Each camera connects as a separate participant.
        - Track name = camera_id so the frontend can identify streams.
    """

    def __init__(
        self,
        settings: LiveKitSettings,
        buffer: FrameBuffer | None = None,
        overlay_store: DetectionOverlayStore | None = None,
    ) -> None:
        self._settings = settings
        self._buffer = buffer or frame_buffer
        self._overlay_store = overlay_store or detection_overlay_store
        # tenant_id → {camera_id → Room}
        self._rooms: dict[str, dict[str, rtc.Room]] = defaultdict(dict)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sources: dict[str, rtc.VideoSource] = {}
        self._logger = get_logger("livekit_publisher")
        self._running = False

    @property
    def active_rooms(self) -> dict[str, list[str]]:
        """Map of tenant_id → list of camera_ids with active connections."""
        return {
            tenant_id: list(cameras.keys())
            for tenant_id, cameras in self._rooms.items()
            if cameras
        }

    async def start(self, cameras: list[CameraConfig]) -> None:
        """Start publishing frames for each camera to its tenant's room.

        Args:
            cameras: Camera configurations to publish. Cameras are grouped
                     by tenant_id — each tenant gets one LiveKit room.
        """
        self._running = True

        # Group cameras by tenant
        by_tenant: dict[str, list[CameraConfig]] = defaultdict(list)
        for cam in cameras:
            by_tenant[cam.tenant_id].append(cam)

        for tenant_id, tenant_cameras in by_tenant.items():
            for cam in tenant_cameras:
                try:
                    await self._connect_camera(tenant_id, cam.camera_id)
                    self._logger.info(
                        "livekit_camera_connected",
                        tenant_id=tenant_id,
                        camera_id=cam.camera_id,
                        room=tenant_id,
                    )
                except Exception as e:
                    self._logger.error(
                        "livekit_camera_connect_failed",
                        tenant_id=tenant_id,
                        camera_id=cam.camera_id,
                        error=str(e),
                    )

        self._logger.info(
            "livekit_publisher_started",
            rooms=self.active_rooms,
        )

    async def _connect_camera(self, tenant_id: str, camera_id: str) -> None:
        """Connect one camera as a participant to its tenant's LiveKit room.

        Args:
            tenant_id: Tenant whose room to join.
            camera_id: Camera identifier (used as participant identity suffix
                       and track name).
        """
        # 1) Create room and connect as publisher
        self._logger.info("livekit_connecting", tenant_id=tenant_id, camera_id=camera_id, url=self._settings.url)
        room = rtc.Room()
        token = generate_publisher_token(tenant_id, camera_id, self._settings)
        await room.connect(
            self._settings.url,
            token,
            options=rtc.RoomOptions(auto_subscribe=False),
        )
        self._rooms[tenant_id][camera_id] = room
        self._logger.info("livekit_room_connected", tenant_id=tenant_id, camera_id=camera_id)

        # 2) Create video source and publish track (track name = camera_id)
        source = rtc.VideoSource(
            self._settings.publish_width,
            self._settings.publish_height,
        )
        track = rtc.LocalVideoTrack.create_video_track(camera_id, source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        await room.local_participant.publish_track(track, options)
        self._sources[camera_id] = source
        self._logger.info("livekit_track_published", tenant_id=tenant_id, camera_id=camera_id, track_name=camera_id)

        # 3) Start background publish loop
        task = asyncio.create_task(self._publish_loop(camera_id))
        self._tasks[camera_id] = task

    async def _publish_loop(self, camera_id: str) -> None:
        """Continuously read frames from FrameBuffer and publish to LiveKit.

        Args:
            camera_id: Camera to publish frames for.
        """
        interval = 1.0 / self._settings.publish_fps
        source = self._sources[camera_id]
        width = self._settings.publish_width
        height = self._settings.publish_height
        published_count = 0
        empty_count = 0
        log_every = int(self._settings.publish_fps * 5)  # log every ~5 seconds

        while self._running:
            try:
                bgr_frame = self._buffer.get(camera_id)
                if bgr_frame is not None:
                    display_frame = bgr_frame
                    if display_frame.shape[1] != width or display_frame.shape[0] != height:
                        display_frame = cv2.resize(display_frame, (width, height))

                    display_frame = display_frame.copy()
                    detections = await self._overlay_store.get_remote(camera_id)
                    if detections:
                        for detection in detections:
                            if len(detection.bbox) != 4:
                                continue

                            x, y, w, h = [int(round(v)) for v in detection.bbox]
                            x1 = max(0, x)
                            y1 = max(0, y)
                            x2 = max(x1 + 1, x1 + max(0, w))
                            y2 = max(y1 + 1, y1 + max(0, h))
                            label = f"{detection.label} {detection.confidence:.2f}"

                            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                            (text_width, text_height), _ = cv2.getTextSize(
                                label,
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.5,
                                1,
                            )
                            text_top = max(18, y1 - text_height - 8)
                            cv2.rectangle(
                                display_frame,
                                (x1, text_top),
                                (x1 + text_width + 8, text_top + text_height + 8),
                                (0, 0, 255),
                                -1,
                            )
                            cv2.putText(
                                display_frame,
                                label,
                                (x1 + 4, text_top + text_height + 2),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.5,
                                (255, 255, 255),
                                1,
                                cv2.LINE_AA,
                            )

                    rgba_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGBA)
                    video_frame = rtc.VideoFrame(
                        width,
                        height,
                        rtc.VideoBufferType.RGBA,
                        rgba_frame.tobytes(),
                    )
                    source.capture_frame(video_frame)
                    published_count += 1
                else:
                    empty_count += 1

                # Periodic diagnostic log
                total = published_count + empty_count
                if total > 0 and total % log_every == 0:
                    self._logger.info(
                        "publish_loop_stats",
                        camera_id=camera_id,
                        published=published_count,
                        empty=empty_count,
                        buffer_cameras=self._buffer.camera_ids,
                    )

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(
                    "livekit_publish_error",
                    camera_id=camera_id,
                    error=str(e),
                )
                await asyncio.sleep(interval)

    async def stop(self) -> None:
        """Disconnect all rooms and cancel all publish tasks."""
        self._running = False

        # 1) Cancel all publish tasks
        for camera_id, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 2) Disconnect all rooms across all tenants
        for tenant_id, cameras in self._rooms.items():
            for camera_id, room in cameras.items():
                try:
                    await room.disconnect()
                    self._logger.info(
                        "livekit_room_disconnected",
                        tenant_id=tenant_id,
                        camera_id=camera_id,
                    )
                except Exception as e:
                    self._logger.error(
                        "livekit_room_disconnect_error",
                        tenant_id=tenant_id,
                        camera_id=camera_id,
                        error=str(e),
                    )

        self._rooms.clear()
        self._tasks.clear()
        self._sources.clear()
        self._logger.info("livekit_publisher_stopped")