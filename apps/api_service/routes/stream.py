from fastapi import APIRouter
from pydantic import BaseModel

from shared.config.settings import AppSettings
from livekit_service.livekit_tokens import generate_subscriber_token

router = APIRouter(prefix="/api/v1/livekit", tags=["livekit"])


class TokenRequest(BaseModel):
    tenant_id: str
    viewer_id: str | None = None


class TokenResponse(BaseModel):
    token: str


@router.post("/token", response_model=TokenResponse)
def create_token(req: TokenRequest):
    settings = AppSettings()
    viewer_id = req.viewer_id or "viewer"
    token = generate_subscriber_token(req.tenant_id, viewer_id, settings.livekit)
    return TokenResponse(token=token)
