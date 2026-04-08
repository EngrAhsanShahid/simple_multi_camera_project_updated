# pipelines/dummy/pipeline.py
"""
File Use:
    A dummy pipeline for testing the full system before real AI models
    are integrated. Produces fake detections with configurable delay
    to simulate inference time.

Implements:
    - DummyPipeline (test pipeline with fake detections)

Depends On:
    - pipelines.base_pipeline
    - shared.contracts.frame_packet
    - shared.contracts.pipeline_result

Used By:
    - tests/test_pipeline_runner.py
    - Development and integration testing
"""

import random
import time

from pipelines.base_pipeline import BasePipeline
from shared.contracts.frame_packet import FramePacket
from shared.contracts.pipeline_result import Detection, PipelineResult
from shared.utils.logging import get_logger


class DummyPipeline(BasePipeline):
    """Test pipeline that produces fake detections.

    Responsibilities:
        - Simulate AI inference with configurable delay.
        - Return random bounding boxes with configurable labels.
        - Allow testing the full pipeline flow without real models.
    """

    def __init__(self, config: dict) -> None:
        """Initialize the dummy pipeline.

        Args:
            config: Configuration dict with optional keys:
                - pipeline_id: str (default "dummy")
                - inference_delay_ms: float (default 50.0)
                - labels: list[str] (default ["person", "helmet_missing"])
                - max_detections: int (default 3)
                - detection_probability: float (default 0.8)
        """
        self._pipeline_id: str = config.get("pipeline_id", "dummy")
        self._inference_delay_ms: float = config.get("inference_delay_ms", 50.0)
        self._labels: list[str] = config.get("labels", ["person", "helmet_missing"])
        self._max_detections: int = config.get("max_detections", 3)
        self._detection_probability: float = config.get("detection_probability", 0.8)
        self._logger = get_logger("dummy_pipeline", pipeline_id=self._pipeline_id)
        self._logger.info("pipeline_initialized", config=config)

    def process(self, frame_packet: FramePacket) -> PipelineResult:
        """Process a frame and return fake detections.

        Args:
            frame_packet: The frame and metadata to process.

        Returns:
            PipelineResult with randomly generated detections.
        """
        start = time.monotonic()

        # 1) Simulate inference delay
        if self._inference_delay_ms > 0:
            time.sleep(self._inference_delay_ms / 1000.0)

        # 2) Generate random detections
        detections: list[Detection] = []
        if random.random() < self._detection_probability:
            num_detections = random.randint(1, self._max_detections)
            for _ in range(num_detections):
                x = random.uniform(0, frame_packet.width * 0.7)
                y = random.uniform(0, frame_packet.height * 0.7)
                w = random.uniform(30, frame_packet.width * 0.3)
                h = random.uniform(50, frame_packet.height * 0.3)
                detections.append(
                    Detection(
                        label=random.choice(self._labels),
                        bbox=[x, y, w, h],
                        confidence=round(random.uniform(0.5, 0.99), 2),
                    )
                )

        elapsed_ms = (time.monotonic() - start) * 1000.0

        return PipelineResult(
            tenant_id=frame_packet.tenant_id,
            camera_id=frame_packet.camera_id,
            frame_id=frame_packet.frame_id,
            pipeline_id=self._pipeline_id,
            timestamp=frame_packet.timestamp,
            detections=detections,
            inference_time_ms=round(elapsed_ms, 2),
        )
