# shared/config/camera_seeder.py
"""
File Use:
    Seeds camera configurations from a JSON file into MongoDB.
    Used on first run (or when new cameras are added to the JSON file)
    to populate the database. Uses upsert semantics so existing cameras
    are updated, not duplicated.

Implements:
    - seed_cameras (load JSON → upsert into MongoDB)

Depends On:
    - shared.storage.mongo_client
    - shared.utils.logging

Used By:
    - run.py (orchestrator, on startup)
"""

import json
from pathlib import Path

from shared.storage.mongo_client import MongoAlertStore
from shared.utils.logging import get_logger

logger = get_logger("camera_seeder")

DEFAULT_CAMERAS_PATH = "config/cameras.json"


def seed_cameras(
    mongo_store: MongoAlertStore,
    json_path: str = DEFAULT_CAMERAS_PATH,
) -> int:
    """Load cameras from a JSON file and upsert into MongoDB.

    Reads the cameras array from the JSON file and upserts each camera
    into MongoDB. Existing cameras are updated with the latest config;
    new cameras are inserted.

    Args:
        mongo_store: MongoDB store instance with camera operations.
        json_path: Path to the cameras JSON file.

    Returns:
        Number of cameras processed (upserted).
    """
    path = Path(json_path)
    if not path.exists():
        logger.warning("cameras_json_not_found", path=str(path))
        return 0

    with open(path) as f:
        data = json.load(f)

    cameras = data.get("cameras", [])
    if not cameras:
        logger.info("no_cameras_in_json", path=str(path))
        return 0

    count = 0
    for camera_dict in cameras:
        try:
            mongo_store.upsert_camera(camera_dict)
            count += 1
        except Exception as e:
            logger.error(
                "camera_seed_failed",
                camera_id=camera_dict.get("camera_id", "unknown"),
                error=str(e),
            )

    logger.info("cameras_seeded", count=count, source=str(path))
    return count
