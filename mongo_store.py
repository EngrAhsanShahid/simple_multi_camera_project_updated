import os
from typing import Any

from dotenv import load_dotenv
from pymongo import DESCENDING, MongoClient

load_dotenv()


class MongoAlertStore:
    def __init__(self) -> None:
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        mongo_db = os.getenv("MONGO_DB", "nexa_simple")

        self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        self.db = self.client[mongo_db]
        self.alerts = self.db["alerts"]
        self.cameras = self.db["cameras"]
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        self.alerts.create_index(
            [("tenant_id", 1), ("camera_id", 1), ("timestamp", DESCENDING)],
            background=True,
        )
        self.alerts.create_index("alert_id", unique=True, background=True)

    def insert_alert(self, alert_dict: dict[str, Any]) -> str:
        self.alerts.insert_one(alert_dict)
        return str(alert_dict.get("alert_id", ""))

    def get_cameras(self) -> list[dict[str, Any]]:
        return list(self.cameras.find({"enabled": True}))

    def get_camera(self, tenant_id: str, camera_id: str) -> dict[str, Any] | None:
        return self.cameras.find_one({"tenant_id": tenant_id, "camera_id": camera_id, "enabled": True})

    def get_cameras_by_ids(self, camera_ids: list[tuple[str, str]]) -> list[dict[str, Any]]:
        if not camera_ids:
            return []

        or_filters = [{"tenant_id": tenant_id, "camera_id": camera_id} for tenant_id, camera_id in camera_ids]
        found = list(self.cameras.find({"enabled": True, "$or": or_filters}))
        camera_map = { (cam["tenant_id"], cam["camera_id"]): cam for cam in found }
        return [camera_map[(tenant_id, camera_id)] for tenant_id, camera_id in camera_ids if (tenant_id, camera_id) in camera_map]

    def close(self) -> None:
        self.client.close()
