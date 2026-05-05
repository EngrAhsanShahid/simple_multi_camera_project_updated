# shared/storage/minio_client.py
"""
File Use:
    MinIO client for storing and retrieving evidence snapshots
    (annotated frames as JPEG images).

Implements:
    - MinioSnapshotStore

Depends On:
    - minio
    - numpy
    - cv2
    - shared.config.settings
    - shared.utils.logging

Used By:
    - main.py (snapshot + clip storage)
"""

import io
from datetime import timedelta

import cv2
import numpy as np
from minio import Minio
from minio.error import S3Error

from config.settings import MinioSettings
from utils.logging import get_logger


class MinioSnapshotStore:
    """MinIO store for alert evidence snapshots.

    Responsibilities:
        - Connect to MinIO and ensure the evidence bucket exists.
        - Encode numpy frames to JPEG and upload.
        - Generate presigned URLs for snapshot retrieval.
        - Download raw snapshot bytes.
    """

    def __init__(self, settings: MinioSettings | None = None) -> None:
        """Initialize the MinIO snapshot store.

        Args:
            settings: MinIO connection settings. Uses defaults if None.
        """
        self._settings = settings or MinioSettings()
        self._logger = get_logger("minio_store")
        self._bucket = self._settings.bucket

        # 1) Connect to MinIO
        self._client = Minio(
            self._settings.endpoint,
            access_key=self._settings.access_key,
            secret_key=self._settings.secret_key,
            secure=self._settings.secure,
        )

        # 2) Ensure bucket exists
        self._ensure_bucket()

        self._logger.info(
            "minio_store_connected",
            endpoint=self._settings.endpoint,
            bucket=self._bucket,
        )

    def _ensure_bucket(self) -> None:
        """Create the evidence bucket if it doesn't exist."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                self._logger.info("minio_bucket_created", bucket=self._bucket)
        except S3Error as e:
            self._logger.error("minio_bucket_error", error=str(e))
            raise

    def store_snapshot(
        self,
        tenant_id: str,
        camera_id: str,
        alert_id: str,
        frame: np.ndarray,
        quality: int = 85,
    ) -> str:
        """Encode a frame to JPEG and upload to MinIO.

        Args:
            tenant_id: Tenant identifier for path partitioning.
            camera_id: Camera identifier for path partitioning.
            alert_id: Alert identifier for unique naming.
            frame: The numpy frame (BGR, as from OpenCV).
            quality: JPEG compression quality (0-100).

        Returns:
            The object path (key) in MinIO.
        """
        # 1) Encode frame to JPEG bytes
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        success, buffer = cv2.imencode(".jpg", frame, encode_params)
        if not success:
            raise RuntimeError("Failed to encode frame to JPEG")

        jpeg_bytes = buffer.tobytes()

        # 2) Build object path
        object_path = f"{tenant_id}/{camera_id}/alerts/{alert_id}/snapshot.jpg"

        # 3) Upload to MinIO
        self._client.put_object(
            bucket_name=self._bucket,
            object_name=object_path,
            data=io.BytesIO(jpeg_bytes),
            length=len(jpeg_bytes),
            content_type="image/jpeg",
        )

        self._logger.debug(
            "snapshot_stored",
            object_path=object_path,
            size_bytes=len(jpeg_bytes),
        )
        return f"{self._bucket}/{object_path}"

    def get_snapshot_url(
        self,
        object_path: str,
        expires: timedelta | None = None,
    ) -> str:
        """Generate a presigned URL for a snapshot.

        Args:
            object_path: The object key in MinIO.
            expires: URL expiration time. Defaults to 1 hour.

        Returns:
            Presigned URL string.
        """
        if expires is None:
            expires = timedelta(hours=1)

        return self._client.presigned_get_object(
            bucket_name=self._bucket,
            object_name=object_path,
            expires=expires,
        )

    def store_clip(
        self,
        tenant_id: str,
        camera_id: str,
        alert_id: str,
        clip_bytes: bytes,
    ) -> str:
        """Upload a WebM clip to MinIO.

        Args:
            tenant_id: Tenant identifier for path partitioning.
            camera_id: Camera identifier for path partitioning.
            alert_id: Alert identifier for unique naming.
            clip_bytes: Raw WebM bytes.

        Returns:
            The object path (key) in MinIO.
        """
        object_path = f"{tenant_id}/{camera_id}/alerts/{alert_id}/clip.webm"

        self._client.put_object(
            bucket_name=self._bucket,
            object_name=object_path,
            data=io.BytesIO(clip_bytes),
            length=len(clip_bytes),
            content_type="video/webm",
        )

        self._logger.debug(
            "clip_stored",
            object_path=object_path,
            size_bytes=len(clip_bytes),
        )
        return f"{self._bucket}/{object_path}"

    def get_clip_url(
        self,
        object_path: str,
        expires: timedelta | None = None,
    ) -> str:
        """Generate a presigned URL for a clip.

        Args:
            object_path: The object key in MinIO.
            expires: URL expiration time. Defaults to 1 hour.

        Returns:
            Presigned URL string.
        """
        if expires is None:
            expires = timedelta(hours=1)

        return self._client.presigned_get_object(
            bucket_name=self._bucket,
            object_name=object_path,
            expires=expires,
        )

    def get_snapshot(self, object_path: str) -> bytes:
        """Download raw snapshot bytes.

        Args:
            object_path: The object key in MinIO.

        Returns:
            Raw JPEG bytes.
        """
        response = self._client.get_object(
            bucket_name=self._bucket,
            object_name=object_path,
        )
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
