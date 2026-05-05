# shared/storage/__init__.py
"""Storage layer: MinIO snapshot/clip storage."""

from storage.minio_client import MinioSnapshotStore

__all__ = ["MinioSnapshotStore"]
