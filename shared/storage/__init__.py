# shared/storage/__init__.py
"""
File Use:
    Package initializer for the storage layer. Re-exports storage
    client classes for convenient imports.

Implements:
    - Package-level exports

Depends On:
    - shared.storage.mongo_client
    - shared.storage.minio_client

Used By:
    - apps/event_processor/main.py
    - apps/api_service (future)
"""

from shared.storage.minio_client import MinioSnapshotStore
from shared.storage.mongo_client import MongoAlertStore

__all__ = [
    "MongoAlertStore",
    "MinioSnapshotStore",
]
