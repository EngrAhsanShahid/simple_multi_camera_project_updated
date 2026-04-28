import argparse
import asyncio
import collections
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import cv2
from dotenv import load_dotenv
from pydantic import ValidationError

from aggregator import FrameAggregator
from shared.contracts import CameraConfig, FramePacket
from frame_sampler import FrameSampler
from mongo_store import MongoAlertStore
from pipeline_manager import PipelineManager
from source_reader import SourceReader
from shared.utils.logging import get_logger
from shared.config.settings import AppSettings
from shared.config.rule_loader import load_rules
from apps.event_processor.rule_engine import RuleEngine
from shared.storage.minio_client import MinioSnapshotStore
from apps.media_service.frame_buffer import frame_buffer

try:
    from livekit_service.livekit_publisher import LiveKitPublisher
except Exception:
    LiveKitPublisher = None

load_dotenv()
logger = get_logger("main")

# Single source of truth for env-backed configuration.
_settings = AppSettings()

QUEUE_MAXSIZE = _settings.pipeline_queue_size
AGGREGATOR_TIMEOUT_MS = int(_settings.aggregation_timeout_ms)
ALERT_VIDEO_FPS = int(_settings.alert_clip_fps)
ALERT_FRAMES_BEFORE = _settings.alert_frames_before
ALERT_FRAMES_AFTER = _settings.alert_frames_after

# Global in-memory ring buffer to hold past frames per camera.
# Sized to cover the pre/post window needed for alert clips.
camera_ring_buffers: dict[str, collections.deque] = collections.defaultdict(
    lambda: collections.deque(maxlen=ALERT_FRAMES_BEFORE + ALERT_FRAMES_AFTER + 1)
)

logger.info(
    "configuration_loaded",
    queue_maxsize=QUEUE_MAXSIZE,
    aggregator_timeout_ms=AGGREGATOR_TIMEOUT_MS,
    alert_video_fps=ALERT_VIDEO_FPS,
    alert_frames_before=ALERT_FRAMES_BEFORE,
    alert_frames_after=ALERT_FRAMES_AFTER,
)


def _safe_name(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value))
    return text.strip("_") or "item"


def _annotate_alert_frame(frame, alert):
    image = frame.copy()
    bbox = (alert.details or {}).get("bbox")
    if bbox and len(bbox) == 4:
        x, y, w, h = [int(round(v)) for v in bbox]
        x2 = max(0, x + max(0, w))
        y2 = max(0, y + max(0, h))
        x = max(0, x)
        y = max(0, y)
        cv2.rectangle(image, (x, y), (x2, y2), (0, 0, 255), 2)
        cv2.putText(
            image,
            str(alert.alert_type),
            (x, max(20, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    return image


def _annotate_processed_frame(frame, frame_result):
    image = frame.copy()
    for pipeline_id, pipeline_result in frame_result.results.items():
        for detection in pipeline_result.detections:
            if len(detection.bbox) != 4:
                continue

            x, y, w, h = [int(round(v)) for v in detection.bbox]
            x2 = max(0, x + max(0, w))
            y2 = max(0, y + max(0, h))
            x = max(0, x)
            y = max(0, y)

            cv2.rectangle(image, (x, y), (x2, y2), (0, 255, 0), 2)
            label = f"{pipeline_id}:{detection.label} {detection.confidence:.2f}"
            cv2.putText(
                image,
                label,
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

    status = "complete" if frame_result.is_complete else "partial"
    overlay = f"{frame_result.camera_id} | {status} | pipelines {frame_result.received_pipelines}/{frame_result.expected_pipelines}"
    overlay_width = min(760, 18 * len(overlay))
    cv2.rectangle(image, (8, 8), (8 + overlay_width, 38), (0, 0, 0), -1)
    cv2.putText(
        image,
        overlay,
        (14, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return image


def _annotate_pipeline_frame(frame, pipeline_result, pipeline_count: int):
    image = frame.copy()
    for detection in pipeline_result.detections:
        if len(detection.bbox) != 4:
            continue

        x, y, w, h = [int(round(v)) for v in detection.bbox]
        x2 = max(0, x + max(0, w))
        y2 = max(0, y + max(0, h))
        x = max(0, x)
        y = max(0, y)

        cv2.rectangle(image, (x, y), (x2, y2), (255, 165, 0), 2)
        label = f"{pipeline_result.pipeline_id}:{detection.label} {detection.confidence:.2f}"
        cv2.putText(
            image,
            label,
            (x, max(20, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 165, 0),
            2,
            cv2.LINE_AA,
        )

    overlay = f"{pipeline_result.camera_id} | partial | pipelines 1/{pipeline_count}"
    overlay_width = min(760, 18 * len(overlay))
    cv2.rectangle(image, (8, 8), (8 + overlay_width, 38), (0, 0, 0), -1)
    cv2.putText(
        image,
        overlay,
        (14, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return image


def save_alert_snapshot(frame, alert, minio_store: MinioSnapshotStore) -> str | None:
    try:
        image = _annotate_alert_frame(frame, alert)
        return minio_store.store_snapshot(
            tenant_id=str(alert.tenant_id),
            camera_id=str(alert.camera_id),
            alert_id=str(alert.alert_id),
            frame=image,
        )
    except Exception as exc:
        logger.error(f"Failed to save alert snapshot: {exc}")
    return None


def save_alert_clip(frame, alert, minio_store: MinioSnapshotStore) -> str | None:
    try:
        if not camera_ring_buffers.get(alert.camera_id):
            return None

        fd, temp_name = tempfile.mkstemp(suffix=".webm")
        os.close(fd)
        temp_path = Path(temp_name)
####
        expected_path = f"{minio_store._bucket}/{alert.tenant_id}/{alert.camera_id}/alerts/{alert.alert_id}/clip.webm"

        def _write_clip_thread(temp_path_str):
            try:
                # Wait natively for 'after' frames to accumulate in the ring buffer
                time.sleep(ALERT_FRAMES_AFTER / max(1, ALERT_VIDEO_FPS))

                ring_buffer = camera_ring_buffers.get(alert.camera_id)
                if not ring_buffer:
                    return
                frames = [f for ts, f in ring_buffer]
                if not frames:
                    return

                height, width = frames[0].shape[:2]
                writer = cv2.VideoWriter(temp_path_str, cv2.VideoWriter_fourcc(*"VP80"), ALERT_VIDEO_FPS, (width, height))
                if writer.isOpened():
                    for f in frames:
                        writer.write(f)
                    writer.release()
                
                clip_bytes = Path(temp_path_str).read_bytes()
                Path(temp_path_str).unlink(missing_ok=True)
                
                minio_store.store_clip(
                    tenant_id=str(alert.tenant_id),
                    camera_id=str(alert.camera_id),
                    alert_id=str(alert.alert_id),
                    clip_bytes=clip_bytes,
                )
            except Exception as exc:
                logger.error(f"Failed to process video clip in thread: {exc}")

        t = threading.Thread(target=_write_clip_thread, args=(
            str(temp_path),
        ), daemon=True)
        t.start()
        
        return expected_path
    except Exception as exc:
        logger.error(f"Failed to initialize alert clip thread: {exc}")
    return None

async def put_latest(queue: asyncio.Queue, item: FramePacket) -> None:
    if queue.full():
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            pass
    await queue.put(item)


async def camera_reader_task(camera: CameraConfig, queue: asyncio.Queue) -> None:
    reader = SourceReader(camera)
    sampler = FrameSampler(camera.target_fps)

    if not await asyncio.to_thread(reader.open):
        logger.error(f"Could not open camera: {camera.camera_id}")
        return

    logger.info(f"Camera started: {camera.camera_id} pipelines={camera.pipelines} fps={camera.target_fps}")

    try:
        while True:
            frame = await asyncio.to_thread(reader.read_frame)

            if frame is None:
                if camera.source_type == "rtsp":
                    logger.warning(f"Reconnecting RTSP camera: {camera.camera_id}")
                    await asyncio.to_thread(reader.reconnect)
                    continue
                logger.info(f"Camera ended: {camera.camera_id}")
                break
            
            ts = time.time()
            camera_ring_buffers[camera.camera_id].append((ts, frame.copy()))

            if not sampler.should_sample():
                await asyncio.sleep(0)
                continue

            packet = FramePacket(
                tenant_id=camera.tenant_id,
                camera_id=camera.camera_id,
                timestamp=ts, # use precise timestamp logged in circular buffer
                width=int(frame.shape[1]),
                height=int(frame.shape[0]),
                pipelines=camera.pipelines,
                frame=frame,
            )
            await put_latest(queue, packet)
            await asyncio.sleep(0)
    finally:
        await queue.put(None)
        await asyncio.to_thread(reader.release)


async def _process_and_save_alerts(frame_result, frame, minio_store, mongo_store, rule_engine, is_timeout=False):
    if frame is None:
        return
        
    processed_frame = _annotate_processed_frame(frame, frame_result)
    try:
        frame_buffer.put(frame_result.camera_id, processed_frame)
    except Exception as exc:
        logger.debug(f"frame_buffer.put failed for {frame_result.camera_id}: {exc}")

    alerts = rule_engine.process(frame_result)
    # Clip is frame-level (shared across all alerts from the same frame);
    # snapshot is per-alert so each alert's bbox is highlighted on its own image.
    clip_path = None
    for alert in alerts:
        snapshot_path = save_alert_snapshot(processed_frame, alert, minio_store)
        if clip_path is None:
            clip_path = save_alert_clip(processed_frame, alert, minio_store)

        alert_data = alert.model_dump()
        if snapshot_path is not None:
            alert_data["snapshot_path"] = snapshot_path
        if clip_path is not None:
            alert_data["clip_path"] = clip_path

        alert_id = await asyncio.to_thread(mongo_store.insert_alert, alert_data)
        prefix = "ALERT-TIMEOUT" if is_timeout else "ALERT"
        logger.info(
            f"{prefix}: camera={alert.camera_id} alert_type={alert.alert_type} "
            f"confidence={alert.confidence} alert_id={alert_id} snapshot={snapshot_path} clip={clip_path}"
        )


async def processor_task(
    camera: CameraConfig,
    queue: asyncio.Queue,
    pipeline_manager: PipelineManager,
    aggregator: FrameAggregator,
    rule_engine: RuleEngine,
    minio_store: MinioSnapshotStore | None,
    mongo_store: MongoAlertStore,
) -> None:
    logger.info(f"Processor started: {camera.camera_id}")
    while True:
        frame_packet = await queue.get()
        try:
            if frame_packet is None:
                break

            try:
                results = await pipeline_manager.run_pipelines(frame_packet)
            except Exception as e:
                logger.error(f"Pipeline error for frame {frame_packet.frame_id}: {e}")
                continue

            cached_frame = frame_packet.frame
            for result in results:
                preview_frame = _annotate_pipeline_frame(cached_frame, result, frame_packet.pipeline_count)
                try:
                    frame_buffer.put(camera.camera_id, preview_frame)
                except Exception as exc:
                    logger.debug(f"frame_buffer.put failed for {camera.camera_id}: {exc}")

                frame_result = aggregator.add_result(result, expected_pipelines=frame_packet.pipeline_count)
                if frame_result is not None:
                    alert_frame = None
                    ring = camera_ring_buffers.get(frame_result.camera_id)
                    if ring:
                        for ts, f in reversed(ring):
                            if ts <= frame_result.timestamp:
                                alert_frame = f
                                break
                    if alert_frame is None:
                        alert_frame = cached_frame
                    await _process_and_save_alerts(frame_result, alert_frame, minio_store, mongo_store, rule_engine)

            # Check aggregator timeouts once per frame packet (not per pipeline result).
            timed_out_results = aggregator.check_timeouts()
            for frame_result in timed_out_results:
                alert_frame = None
                ring = camera_ring_buffers.get(frame_result.camera_id)
                if ring:
                    for ts, f in reversed(ring):
                        if ts <= frame_result.timestamp: 
                            alert_frame = f
                            break
                await _process_and_save_alerts(frame_result, alert_frame, minio_store, mongo_store, rule_engine, is_timeout=True)
        finally:
            queue.task_done()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run multi-camera processing")
    parser.add_argument("--tenant_id", required=True, help="Tenant ID")
    parser.add_argument("--camera_ids", nargs="+", required=True, help="List of camera IDs")
    parser.add_argument("--enable_livekit", action="store_true", help="Enable publishing streams to LiveKit")
    args = parser.parse_args()

    app_settings = _settings
    mongo_store = MongoAlertStore(app_settings.mongo)
    camera_data = mongo_store.get_cameras_by_ids([(args.tenant_id, cid) for cid in args.camera_ids])

    cameras = []
    for cam in camera_data:
        try:
            cameras.append(CameraConfig.model_validate(cam))
        except ValidationError as e:
            cam_id = cam.get("camera_id", "unknown")
            logger.error(f"Validation failed for camera {cam_id}: {e}")

    if not cameras:
        logger.error(f"No enabled cameras found for tenant {args.tenant_id} and cameras {args.camera_ids}")
        return

    required_pipelines = set()
    for camera in cameras:
        for p in camera.pipelines:
            required_pipelines.add(p)

    # Pipeline constructors load YOLO/MediaPipe models synchronously — offload
    # to a worker thread so the event loop stays responsive during startup.
    pipeline_manager = await asyncio.to_thread(PipelineManager, required_pipelines, app_settings)
    aggregator = FrameAggregator(timeout_ms=AGGREGATOR_TIMEOUT_MS)
    default_engine, per_camera_engines = load_rules()
    
    # Initialize MinIO (fails if unavailable, no local fallback)
    minio_store = MinioSnapshotStore(app_settings.minio)
    
    livekit_publisher = None

    if getattr(args, "enable_livekit", False) and LiveKitPublisher is not None:
        try:
            livekit_publisher = LiveKitPublisher(app_settings.livekit)
            await livekit_publisher.start(cameras)
        except Exception as e:
            logger.error(f"Failed to start LiveKit publisher: {e}")

    logger.info(f"Available pipelines: {pipeline_manager.available_pipelines()}")
    logger.info(f"Active pipelines initialized: {pipeline_manager.loaded_pipelines()}")
    logger.info(f"Enabled cameras: {[camera.camera_id for camera in cameras]}")

    tasks: list[asyncio.Task] = []
    try:
        for camera in cameras:
            queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
            tasks.append(asyncio.create_task(camera_reader_task(camera, queue), name=f"reader-{camera.camera_id}"))
            rule_engine = per_camera_engines.get(camera.camera_id, default_engine)
            tasks.append(asyncio.create_task(
                processor_task(camera, queue, pipeline_manager, aggregator, rule_engine, minio_store, mongo_store),
                name=f"processor-{camera.camera_id}",
            ))

        await asyncio.gather(*tasks)
    finally:
        # Stop LiveKit publisher if running
        if livekit_publisher is not None:
            try:
                await livekit_publisher.stop()
            except Exception:
                pass

        try:
            await pipeline_manager.close()
        except Exception:
            pass

        mongo_store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
