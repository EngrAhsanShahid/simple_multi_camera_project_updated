# shared/config/camera_loader.py
"""
File Use:
    Loads camera configurations from a JSON file for MVP usage.
    In later stages, this will be replaced by loading configs from MongoDB.

Implements:
    - load_cameras_from_file (parse JSON config into CameraConfig list)

Depends On:
    - shared.contracts.camera_config

Used By:
    - apps/ingest_router/main.py
    - Application startup
"""

import json
from pathlib import Path

from shared.contracts.camera_config import CameraConfig
from shared.utils.logging import get_logger

logger = get_logger("camera_loader")

DEFAULT_CONFIG_PATH = Path("config/cameras.json")


def load_cameras_from_file(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> list[CameraConfig]:
    """Load camera configurations from a JSON file.

    Args:
        config_path: Path to the cameras JSON config file.

    Returns:
        List of validated CameraConfig objects.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config file contains invalid camera entries.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Camera config file not found: {config_path}")

    with open(config_path, "r") as f:
        raw = json.load(f)

    cameras: list[CameraConfig] = []
    for entry in raw.get("cameras", []):
        try:
            camera = CameraConfig.model_validate(entry)
            cameras.append(camera)
            logger.info(
                "camera_loaded",
                camera_id=camera.camera_id,
                source_type=camera.source_type,
                enabled=camera.enabled,
            )
        except Exception as e:
            logger.error("camera_config_invalid", entry=entry, error=str(e))

    logger.info("cameras_loaded", total=len(cameras))
    return cameras
