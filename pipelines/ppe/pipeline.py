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
    - apps/pipeline_runner/process_manager.py
    - tests/test_ppe_pipeline.py
"""

import json
import time
from pathlib import Path

from ultralytics import YOLO

from pipelines.base_pipeline import BasePipeline
from shared.contracts.frame_packet import FramePacket
from shared.contracts.pipeline_result import Detection, PipelineResult
from shared.utils.logging import get_logger

# Violation class IDs — these labels indicate missing PPE
VIOLATION_CLASS_IDS = {2, 3, 4}  # NO-Hardhat, NO-Mask, NO-Safety Vest

# Default config file path
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


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
        """Initialize the PPE pipeline and load the YOLO model.

        Args:
            config: Configuration dict with keys:
                - pipeline_id: str (default "ppe")
                - model_path: str (path to YOLO .pt file)
                - confidence_threshold: float (default 0.5)
                - max_detections: int (default 50)
                - device: str (default "cpu")
        """
        self._pipeline_id: str = config.get("pipeline_id", "ppe")
        self._model_path: str = config.get("model_path", str(Path(__file__).parent / "models" / "best.pt"))
        self._confidence_threshold: float = config.get("confidence_threshold", 0.5)
        self._max_detections: int = config.get("max_detections", 50)
        self._device: str = config.get("device", "cpu")
        self._logger = get_logger("ppe_pipeline", pipeline_id=self._pipeline_id)

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
        """Pipeline identifier."""
        return self._pipeline_id

    @property
    def class_names(self) -> dict[int, str]:
        """Model class name mapping."""
        return self._model.names

    def process(self, frame_packet: FramePacket) -> PipelineResult:
        """Run PPE detection on a single frame.

        Args:
            frame_packet: The frame and metadata to process.

        Returns:
            PipelineResult with detections. Returns empty result on error.
        """
        start = time.monotonic()

        try:
            # Run YOLO inference
            results = self._model(
                frame_packet.frame,
                conf=self._confidence_threshold,
                device=self._device,
                verbose=False,
            )

            # Extract detections from first result
            detections: list[Detection] = []
            r = results[0]
            boxes = r.boxes

            if boxes is not None and len(boxes) > 0:
                for i, box in enumerate(boxes):
                    if i >= self._max_detections:
                        break

                    # xyxy format -> [x, y, w, h]
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    w = x2 - x1
                    h = y2 - y1

                    class_id = int(box.cls[0].item())
                    confidence = round(float(box.conf[0].item()), 4)
                    label = self._model.names.get(class_id, f"class_{class_id}")

                    detections.append(
                        Detection(
                            label=label,
                            bbox=[round(x1, 1), round(y1, 1), round(w, 1), round(h, 1)],
                            confidence=confidence,
                            metadata={
                                "class_id": class_id,
                                "is_violation": class_id in VIOLATION_CLASS_IDS,
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
