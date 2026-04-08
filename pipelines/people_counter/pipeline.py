# pipelines/people_counter/pipeline.py
"""
File Use:
    YOLO-based people counting pipeline with ByteTrack tracking.
    Detects persons, assigns persistent track IDs across frames,
    and maintains both per-frame visible count and cumulative
    unique person count.

Implements:
    - PeopleCounterPipeline (people detection + tracking + counting)

Depends On:
    - ultralytics
    - pipelines.base_pipeline
    - shared.contracts.frame_packet
    - shared.contracts.pipeline_result
    - shared.utils.logging

Used By:
    - apps/pipeline_runner/process_manager.py
    - tests/test_people_counter.py
"""

import json
import time
from pathlib import Path

from ultralytics import YOLO

from pipelines.base_pipeline import BasePipeline
from shared.contracts.frame_packet import FramePacket
from shared.contracts.pipeline_result import Detection, PipelineResult
from shared.utils.logging import get_logger

# Default config file path
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


def load_pipeline_config(config_path: Path | str | None = None) -> dict:
    """Load pipeline configuration from a JSON file.

    Args:
        config_path: Path to config JSON. Defaults to pipelines/people_counter/config.json.

    Returns:
        Configuration dictionary.
    """
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    with open(path) as f:
        return json.load(f)


class PeopleCounterPipeline(BasePipeline):
    """YOLO-based people counting pipeline with ByteTrack tracking.

    Responsibilities:
        - Detect persons in each frame using YOLO.
        - Track persons across frames using ByteTrack (persistent IDs).
        - Maintain a cumulative set of unique person IDs seen.
        - Report per-frame visible count and total unique count.
        - Never raise — return an empty PipelineResult on failure.
    """

    def __init__(self, config: dict) -> None:
        """Initialize the people counter pipeline and load the YOLO model.

        Args:
            config: Configuration dict with keys:
                - pipeline_id: str (default "people_counter")
                - model_path: str (path to YOLO .pt file)
                - confidence_threshold: float (default 0.5)
                - max_detections: int (default 100)
                - device: str (default "cpu")
                - person_class_id: int (default 0, COCO person class)
        """
        self._pipeline_id: str = config.get("pipeline_id", "people_counter")
        self._model_path: str = config.get(
            "model_path", str(Path(__file__).parent / "models" / "yolo26s.pt")
        )
        self._confidence_threshold: float = config.get("confidence_threshold", 0.5)
        self._max_detections: int = config.get("max_detections", 100)
        self._device: str = config.get("device", "cpu")
        self._person_class_id: int = config.get("person_class_id", 0)
        self._logger = get_logger("people_counter", pipeline_id=self._pipeline_id)

        # Tracking state
        self._seen_ids: set[int] = set()

        # Load YOLO model
        self._model = YOLO(self._model_path)
        self._logger.info(
            "pipeline_initialized",
            model_path=self._model_path,
            person_class=self._model.names.get(self._person_class_id, "unknown"),
            device=self._device,
        )

    @property
    def pipeline_id(self) -> str:
        """Pipeline identifier."""
        return self._pipeline_id

    @property
    def visible_count(self) -> int:
        """Number of people visible in the last processed frame."""
        return self._last_visible_count

    @property
    def total_unique_count(self) -> int:
        """Total unique people seen across all frames."""
        return len(self._seen_ids)

    @property
    def seen_ids(self) -> set[int]:
        """Set of all unique track IDs seen."""
        return self._seen_ids.copy()

    def reset_counts(self) -> None:
        """Reset tracking state and counts."""
        self._seen_ids.clear()
        self._last_visible_count = 0

    _last_visible_count: int = 0

    def process(self, frame_packet: FramePacket) -> PipelineResult:
        """Detect and track persons in a single frame.

        Args:
            frame_packet: The frame and metadata to process.

        Returns:
            PipelineResult with person detections including track IDs,
            visible count, and total unique count in metadata.
        """
        start = time.monotonic()

        try:
            # Run YOLO tracking (ByteTrack)
            results = self._model.track(
                frame_packet.frame,
                persist=True,
                conf=self._confidence_threshold,
                classes=[self._person_class_id],
                device=self._device,
                verbose=False,
            )

            # Extract detections
            detections: list[Detection] = []
            r = results[0]
            boxes = r.boxes

            frame_track_ids: list[int] = []

            if boxes is not None and len(boxes) > 0:
                for i, box in enumerate(boxes):
                    if i >= self._max_detections:
                        break

                    # xyxy -> [x, y, w, h]
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    w = x2 - x1
                    h = y2 - y1

                    confidence = round(float(box.conf[0].item()), 4)

                    # Get track ID (may be None if tracker hasn't assigned yet)
                    track_id = int(box.id[0].item()) if box.id is not None else -1

                    if track_id >= 0:
                        frame_track_ids.append(track_id)
                        self._seen_ids.add(track_id)

                    detections.append(
                        Detection(
                            label="Person",
                            bbox=[round(x1, 1), round(y1, 1), round(w, 1), round(h, 1)],
                            confidence=confidence,
                            metadata={
                                "track_id": track_id,
                                "visible_count": len(boxes),
                                "total_unique_count": len(self._seen_ids),
                            },
                        )
                    )

            self._last_visible_count = len(frame_track_ids) if frame_track_ids else len(detections)

        except Exception as exc:
            self._logger.error("inference_failed", error=str(exc))
            detections = []

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
