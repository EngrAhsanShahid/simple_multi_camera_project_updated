from typing import Any

from pymongo import DESCENDING, MongoClient

from config.settings import MongoSettings

### using
class MongoAlertStore:
    def __init__(self, settings: MongoSettings | None = None) -> None:
        settings = settings or MongoSettings()
        self.client = MongoClient(settings.uri, serverSelectionTimeoutMS=5000)
        self.db = self.client[settings.database]
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

    def get_camera(self, tenant_id: str, camera_id: str) -> dict[str, Any] | None:
        return self.cameras.find_one({"tenant_id": tenant_id, "camera_id": camera_id, "enabled": True})

    def close(self) -> None:
        self.client.close()
