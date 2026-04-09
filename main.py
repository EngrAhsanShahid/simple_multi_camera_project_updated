import argparse
import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from aggregator import FrameAggregator
from alert_engine import AlertEngine
from shared.contracts import CameraConfig, FramePacket
from frame_sampler import FrameSampler
from mongo_store import MongoAlertStore
from pipeline_manager import PipelineManager
from source_reader import SourceReader
from logger import get_logger
from shared.config.settings import AppSettings
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

            # Also write latest frame to shared FrameBuffer so LiveKit publisher
            # (if enabled) can pick it up and publish it as a track.
            try:
                frame_buffer.put(camera.camera_id, frame)
            except Exception:
                pass
            await asyncio.sleep(0)
    finally:
        await queue.put(None)
        await asyncio.to_thread(reader.release)


async def processor_task(
    camera: CameraConfig,
    queue: asyncio.Queue,
    pipeline_manager: PipelineManager,
    aggregator: FrameAggregator,
    alert_engine: AlertEngine,
    mongo_store: MongoAlertStore,
) -> None:
    logger.info(f"Processor started: {camera.camera_id}")
    while True:
        frame_packet = await queue.get()
        try:
            if frame_packet is None:
                break

            results = await pipeline_manager.run_pipelines(frame_packet)

            for result in results:
                frame_result = aggregator.add_result(result, expected_pipelines=frame_packet.pipeline_count)
                if frame_result is not None:
                    alerts = alert_engine.build_alerts(frame_result)
                    for alert in alerts:
                        alert_id = await asyncio.to_thread(mongo_store.insert_alert, alert.model_dump())
                        logger.info(
                            f"ALERT: camera={alert.camera_id} alert_type={alert.alert_type} "
                            f"confidence={alert.confidence} alert_id={alert_id}"
                        )

            timed_out_results = aggregator.check_timeouts()
            for frame_result in timed_out_results:
                alerts = alert_engine.build_alerts(frame_result)
                for alert in alerts:
                    alert_id = await asyncio.to_thread(mongo_store.insert_alert, alert.model_dump())
                    logger.info(
                        f"ALERT-TIMEOUT: camera={alert.camera_id} alert_type={alert.alert_type} "
                        f"confidence={alert.confidence} alert_id={alert_id}"
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
    alert_engine = AlertEngine()
    app_settings = AppSettings()
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
            tasks.append(asyncio.create_task(
                processor_task(camera, queue, pipeline_manager, aggregator, alert_engine, mongo_store),
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
