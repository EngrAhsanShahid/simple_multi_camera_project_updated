import argparse
import asyncio
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import cv2
from dotenv import load_dotenv
from aggregator import FrameAggregator
from shared.contracts import CameraConfig, FramePacket
from frame_sampler import FrameSampler
from mongo_store import MongoAlertStore
from pipeline_manager import PipelineManager
from source_reader import SourceReader
from logger import get_logger
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
logger = get_logger()
logger = get_logger("main")


QUEUE_MAXSIZE = 10
AGGREGATOR_TIMEOUT_MS = 500
ALERT_SNAPSHOT_DIR = Path("alerts")
ALERT_VIDEO_FPS = 5
ALERT_VIDEO_SECONDS = 2


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
        if minio_store is not None:
            return minio_store.store_snapshot(
                tenant_id=str(alert.tenant_id),
                camera_id=str(alert.camera_id),
                alert_id=str(alert.alert_id),
                frame=image,
            )

        snapshot_dir = ALERT_SNAPSHOT_DIR / _safe_name(alert.tenant_id) / _safe_name(alert.camera_id)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{int(alert.timestamp * 1000)}_{_safe_name(alert.alert_type)}_{_safe_name(alert.frame_id)}.jpg"
        file_path = snapshot_dir / file_name
        if cv2.imwrite(str(file_path), image):
            return str(file_path)
        logger.warning(f"Could not write alert snapshot: {file_path}")
    except Exception as exc:
        logger.error(f"Failed to save alert snapshot: {exc}")
    return None


def save_alert_clip(frame, alert, minio_store: MinioSnapshotStore) -> str | None:
    try:
        image = _annotate_alert_frame(frame, alert)
        height, width = image.shape[:2]
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        writer = cv2.VideoWriter(str(temp_path), cv2.VideoWriter_fourcc(*"VP80"), ALERT_VIDEO_FPS, (width, height))
        if not writer.isOpened():
            logger.warning(f"Could not open video writer: {temp_path}")
            temp_path.unlink(missing_ok=True)
            return None

        try:
            total_frames = ALERT_VIDEO_FPS * ALERT_VIDEO_SECONDS
            for _ in range(total_frames):
                writer.write(image)
        finally:
            writer.release()

        clip_bytes = temp_path.read_bytes()
        temp_path.unlink(missing_ok=True)
        if minio_store is not None:
            return minio_store.store_clip(
                tenant_id=str(alert.tenant_id),
                camera_id=str(alert.camera_id),
                alert_id=str(alert.alert_id),
                clip_bytes=clip_bytes,
            )

        clip_dir = ALERT_SNAPSHOT_DIR / _safe_name(alert.tenant_id) / _safe_name(alert.camera_id)
        clip_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{int(alert.timestamp * 1000)}_{_safe_name(alert.alert_type)}_{_safe_name(alert.frame_id)}.webm"
        file_path = clip_dir / file_name
        file_path.write_bytes(clip_bytes)
        return str(file_path)
    except Exception as exc:
        logger.error(f"Failed to save alert clip: {exc}")
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
    
    ###
    # frames_processed = 0
    ###

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

            if not sampler.should_sample():
                await asyncio.sleep(0)
                continue
            

            ############################
            # frames_processed += 1
            # if frames_processed > 50:  # Temporary benchmark limit
            #     logger.info(f"Reached benchmark limit of 50 frames: {camera.camera_id}")
            #     break
            ##############################


            packet = FramePacket(
                tenant_id=camera.tenant_id,
                camera_id=camera.camera_id,
                timestamp=time.time(),
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
    frame_cache: dict[str, object] = {}
    while True:
        frame_packet = await queue.get()
        try:
            if frame_packet is None:
                break

            frame_cache[frame_packet.frame_id] = frame_packet.frame

            results = await pipeline_manager.run_pipelines(frame_packet)

            for result in results:
                cached_frame = frame_cache.get(frame_packet.frame_id)
                if cached_frame is not None:
                    preview_frame = _annotate_pipeline_frame(cached_frame, result, frame_packet.pipeline_count)
                    try:
                        frame_buffer.put(camera.camera_id, preview_frame)
                    except Exception:
                        pass

                frame_result = aggregator.add_result(result, expected_pipelines=frame_packet.pipeline_count)
                if frame_result is not None:
                    cached_frame = frame_cache.get(frame_result.frame_id)
                    if cached_frame is not None:
                        processed_frame = _annotate_processed_frame(cached_frame, frame_result)
                        try:
                            frame_buffer.put(camera.camera_id, processed_frame)
                        except Exception:
                            pass

                    alerts = rule_engine.process(frame_result)
                    snapshot_path = None
                    clip_path = None
                    for alert in alerts:
                        if snapshot_path is None and cached_frame is not None:
                            snapshot_path = save_alert_snapshot(processed_frame, alert, minio_store)
                        if clip_path is None and cached_frame is not None:
                            clip_path = save_alert_clip(processed_frame, alert, minio_store)
                        alert_data = alert.model_dump()
                        if snapshot_path is not None:
                            alert_data["snapshot_path"] = snapshot_path
                        if clip_path is not None:
                            alert_data["clip_path"] = clip_path
                        alert_id = await asyncio.to_thread(mongo_store.insert_alert, alert_data)
                        logger.info(
                            f"ALERT: camera={alert.camera_id} alert_type={alert.alert_type} "
                            f"confidence={alert.confidence} alert_id={alert_id} snapshot={snapshot_path} clip={clip_path}"
                        )
                    frame_cache.pop(frame_result.frame_id, None)

            timed_out_results = aggregator.check_timeouts()
            for frame_result in timed_out_results:
                frame = frame_cache.pop(frame_result.frame_id, None)
                if frame is not None:
                    processed_frame = _annotate_processed_frame(frame, frame_result)
                    try:
                        frame_buffer.put(camera.camera_id, processed_frame)
                    except Exception:
                        pass

                alerts = rule_engine.process(frame_result)
                snapshot_path = None
                clip_path = None
                for alert in alerts:
                    if frame is not None and snapshot_path is None:
                        snapshot_path = save_alert_snapshot(processed_frame, alert, minio_store)
                    if frame is not None and clip_path is None:
                        clip_path = save_alert_clip(processed_frame, alert, minio_store)
                    alert_data = alert.model_dump()
                    if snapshot_path is not None:
                        alert_data["snapshot_path"] = snapshot_path
                    if clip_path is not None:
                        alert_data["clip_path"] = clip_path
                    alert_id = await asyncio.to_thread(mongo_store.insert_alert, alert_data)
                    logger.info(
                        f"ALERT-TIMEOUT: camera={alert.camera_id} alert_type={alert.alert_type} "
                        f"confidence={alert.confidence} alert_id={alert_id} snapshot={snapshot_path} clip={clip_path}"
                    )
        finally:
            queue.task_done()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run multi-camera processing")
    parser.add_argument("--tenant_id", required=True, help="Tenant ID")
    parser.add_argument("--camera_ids", nargs="+", required=True, help="List of camera IDs")
    parser.add_argument("--enable_livekit", action="store_true", help="Enable publishing streams to LiveKit")
    args = parser.parse_args()

    mongo_store = MongoAlertStore()
    camera_data = mongo_store.get_cameras_by_ids([(args.tenant_id, cid) for cid in args.camera_ids])
    
    # Convert old source_path from mongo to new schema before validation
    # In your main() function, update the conversion logic:
    for cam in camera_data:
        if "source_path" in cam:
            source_path = cam["source_path"]
            if source_path.startswith("rtsp://"):
                cam["source_type"] = "rtsp"
                cam["rtsp_url"] = source_path  # Keep for compatibility
                # Keep source_path for backward compatibility
            else:
                cam["source_type"] = "file"
                cam["file_path"] = source_path

    cameras = [CameraConfig.model_validate(cam) for cam in camera_data]

    if not cameras:
        logger.error(f"No enabled cameras found for tenant {args.tenant_id} and cameras {args.camera_ids}")
        return

    pipeline_manager = PipelineManager()
    aggregator = FrameAggregator(timeout_ms=AGGREGATOR_TIMEOUT_MS)
    default_engine, per_camera_engines = load_rules()
    app_settings = AppSettings()
    minio_store = None
    try:
        minio_store = MinioSnapshotStore(app_settings.minio)
    except Exception as exc:
        logger.warning(f"MinIO unavailable, using local alert storage fallback: {exc}")
    livekit_publisher = None

    if getattr(args, "enable_livekit", False) and LiveKitPublisher is not None:
        try:
            livekit_publisher = LiveKitPublisher(app_settings.livekit)
            await livekit_publisher.start(cameras)
        except Exception as e:
            logger.error(f"Failed to start LiveKit publisher: {e}")

    logger.info(f"Available pipelines: {pipeline_manager.available_pipelines()}")
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

        mongo_store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
