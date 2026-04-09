from functools import lru_cache
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from shared.config.settings import AppSettings
from shared.storage.minio_client import MinioSnapshotStore
from shared.storage.mongo_client import MongoAlertStore


router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


class AlertEvidenceResponse(BaseModel):
    alert_id: str
    tenant_id: str
    camera_id: str
    snapshot_path: str | None = None
    clip_path: str | None = None
    snapshot_url: str | None = None
    clip_url: str | None = None


class AlertUrlResponse(BaseModel):
    alert_id: str
    object_path: str
    url: str


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


@lru_cache(maxsize=1)
def get_mongo_store() -> MongoAlertStore:
    return MongoAlertStore(get_settings().mongo)


@lru_cache(maxsize=1)
def get_minio_store() -> MinioSnapshotStore | None:
    try:
        return MinioSnapshotStore(get_settings().minio)
    except Exception:
        return None


def _expires_from_minutes(expires_minutes: int) -> timedelta:
    return timedelta(minutes=max(1, expires_minutes))


def _get_alert_or_404(alert_id: str) -> dict:
    alert = get_mongo_store().get_alert_by_id(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.get("/{alert_id}/evidence", response_model=AlertEvidenceResponse)
def get_alert_evidence(alert_id: str, expires_minutes: int = Query(default=60, ge=1, le=24 * 60)):
    alert = _get_alert_or_404(alert_id)
    minio_store = get_minio_store()
    if minio_store is None:
        raise HTTPException(status_code=503, detail="MinIO is unavailable")
    expires = _expires_from_minutes(expires_minutes)

    snapshot_path = alert.get("snapshot_path")
    clip_path = alert.get("clip_path")

    snapshot_url = minio_store.get_snapshot_url(snapshot_path, expires) if snapshot_path else None
    clip_url = minio_store.get_clip_url(clip_path, expires) if clip_path else None

    return AlertEvidenceResponse(
        alert_id=str(alert.get("alert_id", alert_id)),
        tenant_id=str(alert.get("tenant_id", "")),
        camera_id=str(alert.get("camera_id", "")),
        snapshot_path=snapshot_path,
        clip_path=clip_path,
        snapshot_url=snapshot_url,
        clip_url=clip_url,
    )


@router.get("/{alert_id}/snapshot-url", response_model=AlertUrlResponse)
def get_alert_snapshot_url(alert_id: str, expires_minutes: int = Query(default=60, ge=1, le=24 * 60)):
    alert = _get_alert_or_404(alert_id)
    minio_store = get_minio_store()
    if minio_store is None:
        raise HTTPException(status_code=503, detail="MinIO is unavailable")
    snapshot_path = alert.get("snapshot_path")
    if not snapshot_path:
        raise HTTPException(status_code=404, detail="Snapshot not found for alert")

    url = minio_store.get_snapshot_url(snapshot_path, _expires_from_minutes(expires_minutes))
    return AlertUrlResponse(alert_id=str(alert.get("alert_id", alert_id)), object_path=snapshot_path, url=url)


@router.get("/{alert_id}/clip-url", response_model=AlertUrlResponse)
def get_alert_clip_url(alert_id: str, expires_minutes: int = Query(default=60, ge=1, le=24 * 60)):
    alert = _get_alert_or_404(alert_id)
    minio_store = get_minio_store()
    if minio_store is None:
        raise HTTPException(status_code=503, detail="MinIO is unavailable")
    clip_path = alert.get("clip_path")
    if not clip_path:
        raise HTTPException(status_code=404, detail="Clip not found for alert")

    url = minio_store.get_clip_url(clip_path, _expires_from_minutes(expires_minutes))
    return AlertUrlResponse(alert_id=str(alert.get("alert_id", alert_id)), object_path=clip_path, url=url)