import argparse
import asyncio
import collections
import os
import tempfile
import threading
import time
from pathlib import Path
from queue import Queue

import cv2
from dotenv import load_dotenv
from pydantic import ValidationError

from contracts import CameraConfig, FramePacket, FrameResult
from contracts.camera_config import SourceType
from core.frame_sampler import FrameSampler
from storage.mongo_store import MongoAlertStore
from core.pipeline_manager import PipelineManager
from core.source_reader import SourceReader
from utils.logging import get_logger, setup_logging
from config.settings import AppSettings
from config.rule_loader import load_rules
from core.rule_engine import RuleEngine
from storage.minio_client import MinioSnapshotStore
from core.frame_buffer import frame_buffer
from core.kafka_connect import producer_program

try:
    from livekit.publisher import LiveKitPublisher
except Exception:
    LiveKitPublisher = None

load_dotenv()
setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
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

# Tracks every in-flight clip-writing thread so shutdown can drain them.
_clip_threads: list[threading.Thread] = []

# Set at shutdown so sleeping clip threads wake up and exit instead of writing.
_shutdown_event = threading.Event()

logger.info(
    "configuration_loaded",
    queue_maxsize=QUEUE_MAXSIZE,
    aggregator_timeout_ms=AGGREGATOR_TIMEOUT_MS,
    alert_video_fps=ALERT_VIDEO_FPS,
    alert_frames_before=ALERT_FRAMES_BEFORE,
    alert_frames_after=ALERT_FRAMES_AFTER,
)


def _draw_alert_on_frame(image, alert) -> None:
    """Draw a single alert's bbox + label on the image in-place. Caller controls copy semantics."""
    bbox = (alert.details or {}).get("bbox")
    if not bbox or len(bbox) != 4:
        return

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


def _annotate_alerts(frame, alerts: list):
    """Return a copy of frame with all draw-enabled alerts' bboxes painted on."""
    image = frame.copy()
    for alert in alerts:
        if not (alert.details or {}).get("draw", True):
            continue
        _draw_alert_on_frame(image, alert)
    return image


def save_alert_snapshot(frame, alert, minio_store: MinioSnapshotStore) -> str | None:
    try:
        draw = (alert.details or {}).get("draw", True)
        if draw:
            image = frame.copy()
            _draw_alert_on_frame(image, alert)
        else:
            image = frame
        return minio_store.store_snapshot(
            tenant_id=str(alert.tenant_id),
            camera_id=str(alert.camera_id),
            alert_id=str(alert.alert_id),
            frame=image,
        )
    except Exception as exc:
        logger.error(f"Failed to save alert snapshot: {exc}")
    return None


def save_alert_clip(alert, minio_store: MinioSnapshotStore) -> str | None:
    try:
        if not camera_ring_buffers.get(alert.camera_id):
            return None

        fd, temp_name = tempfile.mkstemp(suffix=".webm")
        os.close(fd)
        temp_path = Path(temp_name)

        expected_path = f"{minio_store._bucket}/{alert.tenant_id}/{alert.camera_id}/alerts/{alert.alert_id}/clip.webm"
        draw = (alert.details or {}).get("draw", True)

        def _write_clip_thread(temp_path_str):
            try:
                # Wait for 'after' frames to accumulate; bail out immediately on shutdown.
                if _shutdown_event.wait(timeout=ALERT_FRAMES_AFTER / max(1, ALERT_VIDEO_FPS)):
                    return

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
                        if draw:
                            annotated = f.copy()
                            _draw_alert_on_frame(annotated, alert)
                            writer.write(annotated)
                        else:
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

        t = threading.Thread(target=_write_clip_thread, args=(str(temp_path),), daemon=True)
        t.start()
        _clip_threads.append(t)

        return expected_path
    except Exception as exc:
        logger.error(f"Failed to initialize alert clip thread: {exc}")
    return None

async def put_latest(queue: asyncio.Queue, item: FramePacket) -> None:
    """Push newest frame into the reader→processor queue, dropping oldest if full.

    Backpressure for live streams: when AI pipelines lag behind the camera FPS,
    the queue saturates. We prefer fresh frames over stale ones, so we evict
    the oldest queued FramePacket. The dropped frame is logged so operators
    can see when/how often the worker is falling behind.
    """
    if queue.full():
        try:
            dropped = queue.get_nowait()
            queue.task_done()
            dropped_age_ms = (time.time() - dropped.timestamp) * 1000.0
            logger.debug(
                "frame_dropped_queue_full",
                camera_id=item.camera_id,
                dropped_frame_id=dropped.frame_id,
                dropped_age_ms=round(dropped_age_ms, 1),
                queue_size=queue.qsize(),
                queue_maxsize=queue.maxsize,
            )
        except asyncio.QueueEmpty:
            pass
    await queue.put(item)
    logger.debug(
        "frame_enqueued",
        camera_id=item.camera_id,
        frame_id=item.frame_id,
        queue_size=queue.qsize(),
        queue_maxsize=queue.maxsize,
    )


async def camera_reader_task(camera: CameraConfig, queue: asyncio.Queue) -> None:
    reader = SourceReader(camera)
    sampler = FrameSampler(camera.target_fps)

    if not await asyncio.to_thread(reader.open):
        logger.error(f"Could not open camera: {camera.camera_id}")
        return

    logger.info(f"Camera started: {camera.camera_id} pipelines={camera.pipelines} fps={camera.target_fps}")

    try:
        while True:
            read_start = time.monotonic()
            frame = await asyncio.to_thread(reader.read_frame)
            read_ms = (time.monotonic() - read_start) * 1000.0

            if frame is None:
                if camera.source_type == SourceType.RTSP:
                    logger.warning(f"Reconnecting RTSP camera: {camera.camera_id}")
                    await asyncio.to_thread(reader.reconnect)
                    continue
                logger.info(f"Camera ended: {camera.camera_id}")
                break
            

            ts = time.time()
            camera_ring_buffers[camera.camera_id].append((ts, frame.copy()))
            
            # FRAME RESIZE
            if frame.shape[1] > 1280:
                frame = cv2.resize(frame, (1280, 720))

            if not sampler.should_sample():
                # logger.debug(
                #     "frame_skipped_by_sampler",
                #     camera_id=camera.camera_id,
                #     read_ms=round(read_ms, 1),
                #     ring_buffer_size=len(camera_ring_buffers[camera.camera_id]),
                # )
                await asyncio.sleep(0)
                continue

            logger.debug(
                "frame_sampled",
                camera_id=camera.camera_id,
                read_ms=round(read_ms, 1),
                target_fps=camera.target_fps,
            )

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


async def _process_and_save_alerts(
    frame_result,
    frame,
    minio_store,
    mongo_store,
    rule_engine,
    livekit_enabled: bool = False,
    is_partial: bool = False,
    alert_queue: Queue = None
):
    if frame is None:
        return

    # Run rules first — drawing is alert-driven, not detection-driven.
    alerts = rule_engine.process(frame_result)

# Live stream: raw frame if no alerts, else annotated with draw-enabled alert bboxes.
    if livekit_enabled:
        live_frame = _annotate_alerts(frame, alerts) if alerts else frame
        try:
            frame_buffer.put(frame_result.camera_id, live_frame)
        except Exception as exc:
            logger.debug(f"frame_buffer.put failed for {frame_result.camera_id}: {exc}")


    if not alerts:
        return
    
    ## For Now do not touch this (Not my concern)
    # Live stream: raw frame if no alerts, else annotated with draw-enabled alert bboxes.
    # if livekit_enabled:
    #     live_frame = _annotate_alerts(frame, alerts) if alerts else frame
    #     try:
    #         frame_buffer.put(frame_result.camera_id, live_frame)
    #     except Exception as exc:
    #         logger.debug(f"frame_buffer.put failed for {frame_result.camera_id}: {exc}")


    for alert in alerts:
        snapshot_path = await asyncio.to_thread(save_alert_snapshot, frame, alert, minio_store) # uploadeds the snapshot to minio and returns the path.
        clip_path = save_alert_clip(alert, minio_store) # saves the clip in minio 

        alert_data = alert.model_dump()
        if snapshot_path is not None:
            alert_data["snapshot_path"] = snapshot_path
        if clip_path is not None:
            alert_data["clip_path"] = clip_path

        # TODO: Drop the alert into a queue consumed by a seperate process that writes that alert into a kafka topic,
        # Which is consumed by another service that writes it into mongodb and other service that sends that notification to the frontend via WS.
        alert_queue.put(alert_data.copy())  # non-blocking put into multiprocessing queue for Kafka producer thread
        alert_id = await asyncio.to_thread(mongo_store.insert_alert, alert_data)

        # prefix = "ALERT-PARTIAL" if is_partial else "ALERT"
        # logger.info(
        #     f"{prefix}: camera={alert.camera_id} alert_type={alert.alert_type} "
        #     f"confidence={alert.confidence} alert_id={alert_id} snapshot={snapshot_path} clip={clip_path}"
        # )


async def processor_task(
    camera: CameraConfig,
    queue: asyncio.Queue,
    pipeline_manager: PipelineManager,
    rule_engine: RuleEngine,
    minio_store: MinioSnapshotStore | None,
    mongo_store: MongoAlertStore,
    pipeline_timeout_sec: float,
    livekit_enabled: bool,
    alert_queue: Queue
) -> None:
    logger.info(f"Processor started: {camera.camera_id}")
    while True:
        frame_packet = await queue.get()
        try:
            if frame_packet is None:
                break

            # How long this frame waited in the queue (reader→processor latency).
            queue_wait_ms = (time.time() - frame_packet.timestamp) * 1000.0
            frame_loop_start = time.monotonic()
            logger.debug(
                "frame_dequeued",
                camera_id=frame_packet.camera_id,
                frame_id=frame_packet.frame_id,
                queue_wait_ms=round(queue_wait_ms, 1),
                queue_size_after_get=queue.qsize(),
            )

            pipeline_start = time.monotonic()
            try:
                results = await pipeline_manager.run_pipelines(
                    frame_packet, timeout_sec=pipeline_timeout_sec
                )
            except Exception as e:
                logger.error(f"Pipeline error for frame {frame_packet.frame_id}: {e}")
                continue
            pipeline_ms = (time.monotonic() - pipeline_start) * 1000.0

            expected = frame_packet.pipeline_count
            received = len(results)
            is_complete = received == expected

            logger.debug(
                "frame_pipelines_done",
                camera_id=frame_packet.camera_id,
                frame_id=frame_packet.frame_id,
                pipeline_ms=round(pipeline_ms, 1),
                expected=expected,
                received=received,
                is_complete=is_complete,
            )

            if received == 0:
                # All pipelines timed out or failed — nothing to do for this frame.
                logger.warning(f"No pipeline results for frame {frame_packet.frame_id} (all timed out or failed)")
                continue

            frame_result = FrameResult(
                tenant_id=frame_packet.tenant_id,
                camera_id=frame_packet.camera_id,
                frame_id=frame_packet.frame_id,
                timestamp=frame_packet.timestamp,
                results={r.pipeline_id: r for r in results},
                expected_pipelines=expected,
                received_pipelines=received,
                is_complete=is_complete,
            )

            alerts_start = time.monotonic()
            await _process_and_save_alerts(
                frame_result,
                frame_packet.frame,
                minio_store,
                mongo_store,
                rule_engine,
                livekit_enabled=livekit_enabled,
                is_partial=not is_complete,
                alert_queue=alert_queue
            )
            alerts_ms = (time.monotonic() - alerts_start) * 1000.0
            total_ms = (time.monotonic() - frame_loop_start) * 1000.0
            logger.debug(
                "frame_processed",
                camera_id=frame_packet.camera_id,
                frame_id=frame_packet.frame_id,
                queue_wait_ms=round(queue_wait_ms, 1),
                pipeline_ms=round(pipeline_ms, 1),
                alerts_ms=round(alerts_ms, 1),
                total_ms=round(total_ms, 1),
                queue_size_after_get=queue.qsize(),
            )
        finally:
            queue.task_done()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run single-camera processing worker")
    parser.add_argument("--tenant_id", required=True, help="Tenant ID")
    parser.add_argument("--camera_id", required=True, help="Camera ID")
    parser.add_argument("--enable_livekit", action="store_true", help="Enable publishing streams to LiveKit")
    args = parser.parse_args()

    # Load global settings and connect to MongoDB for camera config + alerts
    app_settings = _settings
    mongo_store = MongoAlertStore(app_settings.mongo)

    # Kafka queue and process
    alert_queue = Queue()
    stop_event = threading.Event()
    kafka_process = threading.Thread(target=producer_program,args=(alert_queue,os.getenv("BOOTSTRAP_SERVER","localhost:9092"),os.getenv("TOPIC","alerts"),stop_event))
    kafka_process.start()

    

    # Fetch camera from MongoDB and validate against CameraConfig schema
    cam_data = mongo_store.get_camera(args.tenant_id, args.camera_id)
    if cam_data is None:
        logger.error(f"Camera {args.camera_id} not found for tenant {args.tenant_id}")
        return

    try:
        camera = CameraConfig.model_validate(cam_data)
    except ValidationError as e:
        logger.error(f"Validation failed for camera {args.camera_id}: {e}")
        return

    # Load only the pipelines required by this camera (e.g. ["ppe"]).
    # Offload to thread so model loading doesn't block the event loop.
    pipeline_manager = await asyncio.to_thread(PipelineManager, set(camera.pipelines), app_settings)

    # Per-frame timeout: pipelines that don't respond within this window are
    # cancelled and dropped. Whatever returned in time is processed normally.
    pipeline_timeout_sec = AGGREGATOR_TIMEOUT_MS / 1000.0

    # Load alert rules: default rules + camera-specific overrides.
    # Falls back to default_engine if no camera-specific config exists.
    default_engine, per_camera_engines = load_rules()

    # Connect to MinIO for storing snapshots/detections from pipeline results.
    minio_store = MinioSnapshotStore(app_settings.minio)

    # Optional: publish live frames/results to LiveKit (WebRTC streaming platform).
    livekit_publisher = None
    if getattr(args, "enable_livekit", False) and LiveKitPublisher is not None:
        try:
            livekit_publisher = LiveKitPublisher(app_settings.livekit)
            await livekit_publisher.start([camera])
        except Exception as e:
            logger.error(f"Failed to start LiveKit publisher: {e}")

    logger.info(f"Available pipelines: {pipeline_manager.available_pipelines()}")
    logger.info(f"Active pipelines initialized: {pipeline_manager.loaded_pipelines()}")
    logger.info(f"Starting worker for camera: {camera.camera_id}")

    # Create queue for frames between reader and processor tasks.
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

    # Select rule engine: use camera-specific if available, else fall back to default.
    rule_engine = per_camera_engines.get(camera.camera_id, default_engine)

    livekit_enabled = livekit_publisher is not None

    # TaskGroup: if either task crashes, the sibling is cancelled automatically.
    # No leaked tasks on shutdown.
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(camera_reader_task(camera, queue), name=f"reader-{camera.camera_id}")
            tg.create_task(
                processor_task(
                    camera,
                    queue,
                    pipeline_manager,
                    rule_engine,
                    minio_store,
                    mongo_store,
                    pipeline_timeout_sec=pipeline_timeout_sec,
                    livekit_enabled=livekit_enabled,
                    alert_queue=alert_queue
                ),
                name=f"processor-{camera.camera_id}",
            )
    finally:
        logger.info("shutdown_initiated", camera_id=camera.camera_id)

        # 1. Stop LiveKit publish-loop tasks and disconnect rooms.
        if livekit_publisher is not None:
            try:
                await livekit_publisher.stop()
            except Exception:
                pass

        # 2. Close pipeline workers (Redis connections, model resources).
        try:
            await pipeline_manager.close()
        except Exception:
            pass

        # 3. Close MongoDB connection.
        mongo_store.close()

        # 4. Signal shutdown to all clip threads so sleeping ones wake up and exit immediately.
        _shutdown_event.set()

        # 5. Signal Kafka thread and wait up to 5 s — prevents hanging when broker is down.
        stop_event.set()
        kafka_process.join(timeout=5)
        if kafka_process.is_alive():
            logger.warning("kafka_thread_did_not_stop_in_time")

        # 6. Drain in-flight clip threads — they exit quickly now that _shutdown_event is set.
        for t in _clip_threads:
            t.join(timeout=3)

        logger.info("shutdown_complete", camera_id=camera.camera_id)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Stopped by user")
