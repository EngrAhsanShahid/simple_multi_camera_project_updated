# pipelines/weapon/pipeline.py

"""
File Use:
    YOLO-based Weapon detection pipeline.
    Detects weapons (e.g., knives, guns) in video frames and flags violations.

Implements:
    - WeaponPipeline

Depends On:
    - ultralytics
    - pipelines.base_pipeline
    - shared.contracts.frame_packet
    - shared.contracts.pipeline_result
    - shared.utils.logging
"""

import json
import time
from pathlib import Path

from ultralytics import YOLO

from pipelines.base_pipeline import BasePipeline
from shared.contracts.frame_packet import FramePacket
from shared.contracts.pipeline_result import Detection, PipelineResult
from shared.utils.logging import get_logger


# Default config file
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


def load_pipeline_config(config_path: Path | str | None = None) -> dict:
    """Load pipeline configuration from JSON file."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    with open(path) as f:
        return json.load(f)


class WeaponPipeline(BasePipeline):
    """YOLO pipeline for detecting weapons."""

    def __init__(self, config: dict) -> None:
        """Initialize the weapon detection pipeline."""

        self._pipeline_id: str = config.get("pipeline_id", "weapon")

        self._model_path: str = config.get(
            "model_path",
            str(Path(__file__).parent / "models" / "best.pt"),
        )

        self._confidence_threshold: float = config.get("confidence_threshold", 0.4)

        self._max_detections: int = config.get("max_detections", 50)

        self._device: str = config.get("device", "cpu")

        self._img_size: int = config.get("img_size", 640)

        self._logger = get_logger("weapon_pipeline", pipeline_id=self._pipeline_id)

        # Load YOLO model
        self._model = YOLO(self._model_path)

        self._logger.info(
            "pipeline_initialized",
            model_path=self._model_path,
            classes=self._model.names,
            device=self._device,
        )

    @property
    def pipeline_id(self) -> str:
        """Return pipeline identifier."""
        return self._pipeline_id

    @property
    def class_names(self) -> dict[int, str]:
        """Return class names from model."""
        return self._model.names

    def process(self, frame_packet: FramePacket) -> PipelineResult:
        """Run weapon detection on a single frame."""

        start = time.monotonic()

        detections: list[Detection] = []

        try:

            results = self._model(
                frame_packet.frame,
                conf=self._confidence_threshold,
                device=self._device,
                imgsz=self._img_size,
                verbose=False,
            )

            r = results[0]
            boxes = r.boxes

            if boxes is not None and len(boxes) > 0:

                for i, box in enumerate(boxes):

                    if i >= self._max_detections:
                        break

                    class_id = int(box.cls[0].item())
                    label = self._model.names.get(class_id, f"class_{class_id}")

                    x1, y1, x2, y2 = box.xyxy[0].tolist()

                    w = x2 - x1
                    h = y2 - y1

                    confidence = round(float(box.conf[0].item()), 4)

                    detections.append(
                        Detection(
                            label=label,
                            bbox=[
                                round(x1, 1),
                                round(y1, 1),
                                round(w, 1),
                                round(h, 1),
                            ],
                            confidence=confidence,
                            metadata={
                                "class_id": class_id,
                                "is_violation": True,
                            },
                        )
                    )

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