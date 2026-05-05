# pipelines/ppe/pipeline.py
"""
File Use:
    YOLO-based PPE (Personal Protective Equipment) detection pipeline.
    Detects hardhats, masks, safety vests, and their violations on
    construction-site or industrial video frames.

Implements:
    - PPEPipeline (real AI pipeline for PPE detection)

Depends On:
    - ultralytics
    - pipelines.base_pipeline
    - shared.contracts.frame_packet
    - shared.contracts.pipeline_result
    - shared.utils.logging

Used By:
    - pipeline_manager.py (loaded dynamically via build())
"""

import asyncio
import json
import time
from pathlib import Path


from pipelines.base_pipeline import BasePipeline
from contracts.frame_packet import FramePacket
from contracts.pipeline_result import Detection, PipelineResult
from utils.logging import get_logger
from core.dynamic_cache import FrameSimilarCache

from redis_stream_sdk.client import RedisClient
from redis_stream_sdk.producer import StreamProducer
import msgpack
import uuid
# Violation class IDs — these labels indicate missing PPE
VIOLATION_CLASS_IDS = {2, 3, 4}  # NO-Hardhat, NO-Mask, NO-Safety Vest

# Default config file path
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


def build(app_settings) -> "PPEPipeline":
    config = load_pipeline_config()
    config.setdefault("redis_url", app_settings.redis.url)
    return PPEPipeline(config)


def load_pipeline_config(config_path: Path | str | None = None) -> dict:
    """Load pipeline configuration from a JSON file.

    Args:
        config_path: Path to config JSON. Defaults to pipelines/ppe/config.json.

    Returns:
        Configuration dictionary.
    """
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    with open(path) as f:
        return json.load(f)


class PPEPipeline(BasePipeline):
    """YOLO-based PPE detection pipeline.

    Responsibilities:
        - Load a trained YOLO model for PPE detection at init time.
        - Run inference on each frame and return Detection objects.
        - Convert YOLO xyxy bounding boxes to [x, y, w, h] format.
        - Never raise — return an empty PipelineResult on failure.
    """

    def __init__(self, config: dict) -> None:
        self._pipeline_id: str = config.get("pipeline_id", "ppe")
        self._logger = get_logger("ppe_pipeline", pipeline_id=self._pipeline_id)
        self._profile: bool = bool(config.get("profile", False))

        self._logger.info("ppe pipeline_initialized")

        # Redis URL is injected by PipelineManager from AppSettings.redis.url
        # (which itself reads from REDIS__URL in .env). The localhost fallback
        # exists only for direct/test instantiation — production must configure
        # this via .env.
        self._redis_url = config.get("redis_url", "redis://localhost:6379")
        self._result_timeout_sec = float(config.get("result_timeout_sec", 3.0))

        self.client: RedisClient | None = None
        self.redis = None
        self.producer: StreamProducer | None = None
        # self.cache = FrameCache(max_cache_size=5)  # Optional in-memory cache for frames
        self.cache = FrameSimilarCache(max_cache_size=5,diff_threshold=0.5,hash_threshold=2)  # Optional in-memory cache for frames
        self._initialized = False

    async def setup(self) -> None:
        """Async initialization for external services."""
        if self._initialized:
            return

        self.client = RedisClient(url=self._redis_url)
        self.redis = await self.client.get()

        self.producer = StreamProducer(self.redis)

        self._initialized = True
        self._logger.info("redis_connected")

    async def close(self) -> None:
        """Release Redis resources owned by this pipeline."""
        if self.client is not None:
            try:
                await self.client.close()
            except Exception:
                pass
        self.client = None
        self.redis = None
        self.producer = None
        self._initialized = False

    @property
    def pipeline_id(self) -> str:
        """Pipeline identifier."""
        return self._pipeline_id

    async def process(self, frame_packet: FramePacket) -> PipelineResult:
        """Run PPE detection on a single frame.

        Args:
            frame_packet: The frame and metadata to process.

        Returns:
            PipelineResult with detections. Returns empty result on error.
        """
        # ensure redis ready (safe for concurrent calls)
        if not self._initialized:
            await self.setup()
        start = time.monotonic()
        result = self.cache.get(frame=frame_packet.frame)
        if result:
            # self._logger.info("ppe cache HIT!!")
            return result
        try:
            # self._logger.info("ppe cache MISS")
            request_id = str(uuid.uuid4())
            pubsub = await self.producer.subscribe(request_id=request_id)

            t_put = time.monotonic()
            await self.producer.publish_frame(
                stream_name="ppe",
                request_id=request_id,
                frame=frame_packet.frame,
                maxlen=20000,
            )
            put_ms = (time.monotonic() - t_put) * 1000.0

            t_get = time.monotonic()
            try:
                results = await asyncio.wait_for(
                    self.producer.get_results(request_id, pubsub=pubsub),
                    timeout=self._result_timeout_sec,
                )
            except asyncio.TimeoutError:
                self._logger.warning(
                    "ppe_result_timeout",
                    request_id=request_id,
                    timeout_sec=self._result_timeout_sec,
                )
                results = {}
            get_ms = (time.monotonic() - t_get) * 1000.0

            # Extract detections from first result
            detections: list[Detection] = []
            boxes = results.get("boxes", [])
            classes = results.get("classes", [])
            confidences = results.get("confidence", [])
            labels = results.get("labels", [])

            if boxes is not None and len(boxes) > 0:
                for box,class_id,confidence,label in zip(boxes, classes, confidences, labels):

                    # xyxy format -> [x, y, w, h]
                    x1, y1, x2, y2 = box
                    w = int(x2 - x1)
                    h = int(y2 - y1)

                    detections.append(
                        Detection(
                            label=label,
                            bbox=[x1,y1,w,h],
                            confidence=round(confidence,4),
                            metadata={
                                "class_id": class_id,   
                                "is_violation": class_id in VIOLATION_CLASS_IDS,
                            },
                        )
                    )

        except Exception as exc:
            self._logger.error("inference_failed", error=str(exc))
            detections = []
            put_ms = get_ms = 0.0

        elapsed_ms = (time.monotonic() - start) * 1000.0

        if self._profile:
            self._logger.info(
                "ppe_profile",
                frame_id=frame_packet.frame_id,
                redis_put_ms=round(put_ms, 2),
                redis_get_ms=round(get_ms, 2),
                total_ms=round(elapsed_ms, 2),
            )

        result = PipelineResult(
            tenant_id=frame_packet.tenant_id,
            camera_id=frame_packet.camera_id,
            frame_id=frame_packet.frame_id,
            pipeline_id=self._pipeline_id,
            timestamp=frame_packet.timestamp,
            detections=detections,
            inference_time_ms=round(elapsed_ms, 2),
        )
        self.cache.set(frame=frame_packet.frame,inference_result=result)
        return result
