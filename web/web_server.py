"""
Lightweight dev server: serves web/ static files and the two API endpoints
the frontend needs.

Usage:
    python web_server.py [--host 0.0.0.0] [--port 8000]
"""

import argparse
from datetime import timedelta
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from config.settings import AppSettings
from livekit.tokens import generate_subscriber_token

_settings = AppSettings()
_WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="NEXA Web Dev Server")


# ── Token endpoint ──────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    tenant_id: str
    viewer_id: str = "viewer"


@app.post("/api/v1/livekit/token")
def get_token(req: TokenRequest):
    try:
        token = generate_subscriber_token(
            tenant_id=req.tenant_id,
            viewer_id=req.viewer_id,
            settings=_settings.livekit,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"token": token}


# ── Evidence endpoint ───────────────────────────────────────────────────────

@app.get("/api/v1/alerts/{alert_id}/evidence")
def get_evidence(
    alert_id: str,
    expires_minutes: int = Query(default=60, ge=1, le=1440),
):
    from storage.minio_client import MinioSnapshotStore
    from storage.mongo_store import MongoAlertStore

    mongo = MongoAlertStore(_settings.mongo)
    try:
        alert = mongo.alerts.find_one({"alert_id": alert_id})
    finally:
        mongo.close()

    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    expires = timedelta(minutes=expires_minutes)
    minio = MinioSnapshotStore(_settings.minio)

    result: dict = {}
    snapshot_path = alert.get("snapshot_path")
    if snapshot_path:
        # strip bucket prefix if present
        key = snapshot_path.removeprefix(f"{_settings.minio.bucket}/")
        try:
            result["snapshot_url"] = minio.get_snapshot_url(key, expires)
        except Exception:
            pass

    clip_path = alert.get("clip_path")
    if clip_path:
        key = clip_path.removeprefix(f"{_settings.minio.bucket}/")
        try:
            result["clip_url"] = minio.get_clip_url(key, expires)
        except Exception:
            pass

    return result


# ── Static files (web/) ─────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="static")


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("web_server:app", host=args.host, port=args.port, reload=True)
