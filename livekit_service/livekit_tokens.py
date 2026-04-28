# apps/media_service/livekit_tokens.py
"""
File Use:
    JWT token generation for LiveKit room access. Creates short-lived
    tokens with publish or subscribe permissions for tenant rooms.

    Room naming: room = tenant_id (one room per tenant).
    Each camera is a separate participant publishing one track.

Implements:
    - generate_publisher_token (backend camera → tenant room)
    - generate_subscriber_token (frontend viewer → tenant room)

Depends On:
    - livekit-api
    - shared.config.settings

Used By:
    - apps/media_service/livekit_publisher.py
    - apps/api_service/routes/stream.py
"""

from shared.config.settings import LiveKitSettings
import time
import uuid
import jwt
import hashlib


def _manual_token(api_key: str, api_secret: str, identity: str, room: str, can_publish: bool, can_subscribe: bool, ttl: int = 3600) -> str:
    iat = int(time.time())
    payload = {
        "jti": str(uuid.uuid4()),
        "iat": iat,
        "nbf": iat,
        "exp": iat + ttl,
        "iss": api_key,
        "sub": identity,
        "video": {
            "roomJoin": True,
            "room": room,
            "canPublish": can_publish,
            "canSubscribe": can_subscribe,
        },
    }
    return jwt.encode(payload, api_secret, algorithm="HS256")


def _short_identity(prefix: str, identifier: str, max_len: int = 250) -> str:
    """Create a compact identity under the LiveKit limit.

    If the combined identity would exceed `max_len`, hash the identifier
    to keep the identity short while preserving uniqueness.
    """
    if prefix:
        candidate = f"{prefix}_{identifier}"
    else:
        candidate = identifier
    if len(candidate) <= max_len:
        return candidate
    # Use a deterministic short hash (16 hex chars -> 64 bits)
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
    if prefix:
        return f"{prefix}_{digest}"
    return digest


def generate_publisher_token(
    tenant_id: str,
    camera_id: str,
    settings: LiveKitSettings,
) -> str:
    """Create a JWT granting publish access to a tenant's LiveKit room.

    Args:
        tenant_id: Tenant whose room the publisher will join.
        camera_id: Camera identifier (used in participant identity).
        settings: LiveKit connection settings (key, secret).

    Returns:
        Signed JWT string for the publisher participant.
    """
    identity = _short_identity(f"nexa_pub_{tenant_id}", camera_id)
    return _manual_token(
        settings.api_key,
        settings.api_secret,
        identity,
        tenant_id,
        True,
        True,
        settings.token_ttl_sec,
    )


def generate_subscriber_token(
    tenant_id: str,
    viewer_id: str,
    settings: LiveKitSettings,
) -> str:
    """Create a JWT granting subscribe-only access to a tenant's LiveKit room.

    Args:
        tenant_id: Tenant whose room the viewer will join.
        viewer_id: Unique identifier for the viewer.
        settings: LiveKit connection settings (key, secret).

    Returns:
        Signed JWT string for the subscriber participant.
    """
    identity = _short_identity(f"{tenant_id}_viewer", viewer_id)
    return _manual_token(
        settings.api_key,
        settings.api_secret,
        identity,
        tenant_id,
        False,
        True,
        settings.token_ttl_sec,
    )
