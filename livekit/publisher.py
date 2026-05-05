# livekit/publisher.py
"""
File Use:
    LiveKit room publisher for the single-camera worker. Reads annotated
    frames from FrameBuffer and publishes them as a video track to the
    tenant's LiveKit room.

    Room model:
        - One LiveKit room per tenant_id.
        - This worker joins as ONE participant publishing ONE track.
        - Track name = camera_id so the frontend can identify streams.
        - Other workers (other cameras of the same tenant) join the
          same room as additional participants with their own tracks.

Implements:
    - LiveKitPublisher

Depends On:
    - livekit (rtc SDK)
    - core.frame_buffer
    - livekit.tokens
    - config.settings
    - contracts.camera_config
    - utils.logging

Used By:
    - main.py
"""

from __future__ import annotations

import asyncio

import cv2
from livekit import rtc

from core.frame_buffer import FrameBuffer, frame_buffer
from livekit.tokens import generate_publisher_token
from config.settings import LiveKitSettings
from contracts.camera_config import CameraConfig
from utils.logging import get_logger


class LiveKitPublisher:
    """Publishes one camera's annotated frames to its tenant's LiveKit room."""

    def __init__(
        self,
        settings: LiveKitSettings,
        buffer: FrameBuffer | None = None,
    ) -> None:
        self._settings = settings
        self._buffer = buffer or frame_buffer
        self._tenant_id: str | None = None
        self._camera_id: str | None = None
        self._room: rtc.Room | None = None
        self._source: rtc.VideoSource | None = None
        self._task: asyncio.Task[None] | None = None
        self._logger = get_logger("livekit_publisher")
        self._running = False

    @property
    def is_active(self) -> bool:
        return self._room is not None and self._task is not None

    async def start(self, cameras: list[CameraConfig]) -> None:
        """Connect this worker's camera to its tenant's LiveKit room.

        Args:
            cameras: Single-element list. List form is preserved so the
                     caller in main.py doesn't need to change shape, but
                     this worker only ever publishes one camera.
        """
        if not cameras:
            self._logger.warning("livekit_start_no_cameras")
            return
        if len(cameras) > 1:
            self._logger.warning(
                "livekit_start_multiple_cameras_ignored",
                received=len(cameras),
                using=cameras[0].camera_id,
            )

        cam = cameras[0]
        self._tenant_id = cam.tenant_id
        self._camera_id = cam.camera_id
        self._running = True

        try:
            await self._connect()
            self._logger.info(
                "livekit_publisher_started",
                tenant_id=self._tenant_id,
                camera_id=self._camera_id,
                room=self._tenant_id,
            )
        except Exception as e:
            self._logger.error(
                "livekit_camera_connect_failed",
                tenant_id=self._tenant_id,
                camera_id=self._camera_id,
                error=str(e),
            )
            self._running = False
            raise

    async def _connect(self) -> None:
        """Connect to the tenant's room, publish the track, start the publish loop."""
        assert self._tenant_id and self._camera_id

        self._logger.info(
            "livekit_connecting",
            tenant_id=self._tenant_id,
            camera_id=self._camera_id,
            url=self._settings.url,
        )

        # 1) Join the tenant's room (room name = tenant_id, auto-created on first join).
        room = rtc.Room()
        token = generate_publisher_token(self._tenant_id, self._camera_id, self._settings)
        await room.connect(
            self._settings.url,
            token,
            options=rtc.RoomOptions(auto_subscribe=False),
        )
        self._room = room
        self._logger.info(
            "livekit_room_connected",
            tenant_id=self._tenant_id,
            camera_id=self._camera_id,
        )

        # 2) Publish a video track named after the camera_id.
        source = rtc.VideoSource(
            self._settings.publish_width,
            self._settings.publish_height,
        )
        track = rtc.LocalVideoTrack.create_video_track(self._camera_id, source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        await room.local_participant.publish_track(track, options)
        self._source = source
        self._logger.info(
            "livekit_track_published",
            tenant_id=self._tenant_id,
            camera_id=self._camera_id,
            track_name=self._camera_id,
        )

        # 3) Start the background publish loop.
        self._task = asyncio.create_task(self._publish_loop())

    async def _publish_loop(self) -> None:
        """Read latest frame from FrameBuffer and feed LiveKit at publish_fps."""
        assert self._camera_id and self._source

        interval = 1.0 / self._settings.publish_fps
        width = self._settings.publish_width
        height = self._settings.publish_height
        published_count = 0
        empty_count = 0
        log_every = int(self._settings.publish_fps * 5)  # log every ~5 seconds

        while self._running:
            try:
                bgr_frame = self._buffer.get(self._camera_id)
                if bgr_frame is not None:
                    if bgr_frame.shape[1] != width or bgr_frame.shape[0] != height:
                        bgr_frame = cv2.resize(bgr_frame, (width, height))

                    rgba_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGBA)
                    video_frame = rtc.VideoFrame(
                        width,
                        height,
                        rtc.VideoBufferType.RGBA,
                        rgba_frame.tobytes(),
                    )
                    self._source.capture_frame(video_frame)
                    published_count += 1
                else:
                    empty_count += 1

                total = published_count + empty_count
                if total > 0 and total % log_every == 0:
                    self._logger.debug(
                        "publish_loop_stats",
                        camera_id=self._camera_id,
                        published=published_count,
                        empty=empty_count,
                    )

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(
                    "livekit_publish_error",
                    camera_id=self._camera_id,
                    error=str(e),
                )
                await asyncio.sleep(interval)

    async def stop(self) -> None:
        """Cancel the publish loop and disconnect from the room."""
        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._room is not None:
            try:
                await self._room.disconnect()
                self._logger.info(
                    "livekit_room_disconnected",
                    tenant_id=self._tenant_id,
                    camera_id=self._camera_id,
                )
            except Exception as e:
                self._logger.error(
                    "livekit_room_disconnect_error",
                    tenant_id=self._tenant_id,
                    camera_id=self._camera_id,
                    error=str(e),
                )
            self._room = None

        self._source = None
        self._logger.info("livekit_publisher_stopped")
