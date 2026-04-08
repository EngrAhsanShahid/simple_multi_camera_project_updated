# shared/auth/jwt_handler.py
"""
File Use:
    JWT token creation and validation for access and refresh tokens.

Implements:
    - create_access_token: Generate a short-lived JWT access token.
    - create_refresh_token: Generate a long-lived JWT refresh token with jti.
    - decode_access_token: Validate and decode an access token.
    - decode_refresh_token: Validate and decode a refresh token.

Depends On:
    - jose (python-jose)
    - shared.config.settings

Used By:
    - apps/api_service/routes/auth.py
    - shared/auth/dependencies.py
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jose import JWTError, jwt

from shared.config.settings import AuthSettings


class TokenError(Exception):
    """Raised when a token is invalid, expired, or has wrong type."""


def create_access_token(
    user_id: str,
    tenant_id: str,
    role: str,
    is_platform: bool,
    settings: AuthSettings,
) -> str:
    """Create a short-lived JWT access token.

    Args:
        user_id: The user's unique identifier (becomes 'sub' claim).
        tenant_id: The user's tenant identifier.
        role: The user's role string.
        is_platform: Whether the user is in the platform tenant.
        settings: Auth configuration with secret key and expiry.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "is_platform": is_platform,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token(
    user_id: str,
    settings: AuthSettings,
) -> tuple[str, str]:
    """Create a long-lived JWT refresh token with a unique jti.

    Args:
        user_id: The user's unique identifier.
        settings: Auth configuration with secret key and expiry.

    Returns:
        Tuple of (encoded JWT string, jti string).
    """
    now = datetime.now(timezone.utc)
    jti = str(uuid4())
    payload = {
        "sub": user_id,
        "jti": jti,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_expire_days),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, jti


def decode_access_token(token: str, settings: AuthSettings) -> dict:
    """Decode and validate a JWT access token.

    Args:
        token: The encoded JWT string.
        settings: Auth configuration with secret key.

    Returns:
        Decoded payload dict with sub, tenant_id, role, is_platform.

    Raises:
        TokenError: If the token is invalid, expired, or not an access token.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
    except JWTError as e:
        raise TokenError(f"Invalid token: {e}") from e

    if payload.get("type") != "access":
        raise TokenError("Token is not an access token")

    return payload


def decode_refresh_token(token: str, settings: AuthSettings) -> dict:
    """Decode and validate a JWT refresh token.

    Args:
        token: The encoded JWT string.
        settings: Auth configuration with secret key.

    Returns:
        Decoded payload dict with sub, jti.

    Raises:
        TokenError: If the token is invalid, expired, or not a refresh token.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
    except JWTError as e:
        raise TokenError(f"Invalid token: {e}") from e

    if payload.get("type") != "refresh":
        raise TokenError("Token is not a refresh token")

    return payload