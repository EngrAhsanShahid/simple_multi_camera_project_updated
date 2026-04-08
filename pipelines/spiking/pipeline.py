# pipelines/drinking/pipeline.py

"""
YOLO + MediaPipe-based Spiking/Drinking Detection Pipeline.
Detects hands interacting suspiciously with containers (glasses, bottles, cups).

Implements:
    - DrinkingPipeline

Depends On:
    - ultralytics
    - mediapipe
    - pipelines.base_pipeline
    - shared.contracts.frame_packet
    - shared.contracts.pipeline_result
    - shared.utils.logging
"""

import time
from pathlib import Path
from collections import deque

import cv2
import numpy as np
from ultralytics import YOLO
import mediapipe as mp

from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from pipelines.base_pipeline import BasePipeline
from shared.contracts.frame_packet import FramePacket
from shared.contracts.pipeline_result import Detection, PipelineResult
from shared.utils.logging import get_logger

# ---------------- CONFIG DEFAULTS ---------------- #
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"

# # # Load from config file, with defaults for missing values # # #
CONTAINER_CLASS_IDS = [39, 40, 41]  # bottle, wine glass, cup 
CONF_THRESHOLD_DEFAULT = 0.4        
ALERT_RANGE_PIXELS_DEFAULT = 200    
IMG_SIZE_DEFAULT = 640

# ---------------- PIPELINE ---------------- #
def load_pipeline_config(config_path: Path | str | None = None) -> dict:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    import json
    with open(path) as f:
        return json.load(f)


class DrinkingPipeline(BasePipeline):
    """Spiking/Drinking detection pipeline."""

    def __init__(self, config: dict) -> None:
        self._pipeline_id: str = config.get("pipeline_id", "spiking_pipeline")

        self._confidence_threshold: float = config.get("confidence_threshold", CONF_THRESHOLD_DEFAULT)
        self._alert_range: int = config.get("alert_range_pixels", ALERT_RANGE_PIXELS_DEFAULT)
        self._img_size: int = config.get("img_size", IMG_SIZE_DEFAULT)
        self._device: str = config.get("device", "cpu")

        self._logger = get_logger("spiking_pipeline", pipeline_id=self._pipeline_id)

        # Load YOLO detection model
        self._yolo_model_path: str = config.get("yolo_model_path", str(Path(__file__).parent / "models" / "yolov8m.pt"))
        self._det_model = YOLO(self._yolo_model_path, verbose=False)

        # Load MediaPipe Hand Landmarker
        base_options = python.BaseOptions(
            model_asset_path=config.get(
                "hand_model_path",
                str(Path(__file__).parent / "models" / "hand_landmarker.task")
            )
        )
        mp_options = vision.HandLandmarkerOptions(base_options=base_options, num_hands=2)
        self._detector = vision.HandLandmarker.create_from_options(mp_options)

        self._logger.info(
            "pipeline_initialized",
            yolo_model=self._yolo_model_path,
            hand_model=config.get("hand_model_path", ""),
            device=self._device
        )

    @property
    def pipeline_id(self) -> str:
        return self._pipeline_id

    def _get_glass_bbox(self, det_results):
        for box in det_results.boxes:
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            if cls in CONTAINER_CLASS_IDS and conf > self._confidence_threshold:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                return int(x1), int(y1), int(x2), int(y2)
        return None

    def _check_suspicious(self, finger_point, glass_bbox):
        gx1, gy1, gx2, gy2 = glass_bbox
        fx, fy = finger_point

        if gx1 <= fx <= gx2:
            vertical_distance = gy1 - fy
            if 0 < vertical_distance <= self._alert_range:
                return "suspicious"
            elif fy >= gy1:
                return "inside"
            elif -5 <= vertical_distance <= 0:
                return "closed"
        elif (gx1-20) <= fx <= (gx2+20):
            vertical_distance = gy1 - fy
            if 0 < vertical_distance <= self._alert_range:
                return "anomaly"
        return None

    def process(self, frame_packet: FramePacket) -> PipelineResult:
        start = time.monotonic()
        detections: list[Detection] = []

        frame = frame_packet.frame.copy()
        h, w, _ = frame.shape

        try:
            # ---------------- YOLO Detection ----------------
            det_results = self._det_model(frame, imgsz=self._img_size, conf=self._confidence_threshold, device=self._device, verbose=False)[0]
            glass_bbox = self._get_glass_bbox(det_results)

            if glass_bbox:
                x1, y1, x2, y2 = glass_bbox
                detections.append(
                    Detection(
                        label="glass",
                        bbox=[x1, y1, x2 - x1, y2 - y1],
                        confidence=1.0,
                        metadata={"class_id": -1, "is_violation": False},
                    )
                )

            # ---------------- MediaPipe Hand Detection ----------------
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            results = self._detector.detect(mp_image)

            suspicious_status = None

            if results.hand_landmarks and glass_bbox:
                for hand_landmarks in results.hand_landmarks:
                    # finger dip points
                    knuckle_indices = [7, 11, 15, 19]
                    xs = [hand_landmarks[i].x for i in knuckle_indices]
                    ys = [hand_landmarks[i].y for i in knuckle_indices]
                    fx, fy = int(sum(xs)/len(xs) * w), int(sum(ys)/len(ys) * h)

                    detections.append(
                        Detection(
                            label="finger_tip",
                            bbox=[fx-3, fy-3, 6, 6],
                            confidence=1.0,
                            metadata={"class_id": -1, "is_violation": True},
                        )
                    )

                    suspicious_status = self._check_suspicious((fx, fy), glass_bbox)

                    if suspicious_status:
                        detections.append(
                            Detection(
                                label=f"spiking_{suspicious_status}",
                                bbox=[fx-5, fy-5, 10, 10],
                                confidence=1.0,
                                metadata={"class_id": -1, "is_violation": True},
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