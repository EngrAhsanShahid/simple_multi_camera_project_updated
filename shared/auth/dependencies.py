# shared/auth/dependencies.py
"""
File Use:
    FastAPI dependencies for authentication and authorization.
    Extracts user context from JWT tokens and enforces role checks.

Implements:
    - get_current_user: Decode JWT → CurrentUser
    - require_role: Dependency factory for role-based access control
    - resolve_tenant_id: Determine effective tenant_id for cross-tenant access

Depends On:
    - fastapi
    - shared.auth.jwt_handler
    - shared.auth.models
    - shared.config.settings

Used By:
    - apps/api_service/routes/* (via Depends)
"""

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer

from shared.auth.jwt_handler import TokenError, decode_access_token
from shared.auth.models import CurrentUser
from shared.config.settings import AppSettings
from shared.contracts.user import UserRole

# Token URL points to the login endpoint for OpenAPI docs
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def _get_auth_settings() -> AppSettings:
    """Import get_settings lazily to avoid circular imports."""
    from apps.api_service.dependencies import get_settings
    return get_settings()


async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> CurrentUser:
    """Decode a JWT access token and return the authenticated user context.

    This is the primary authentication dependency. All protected routes
    should depend on this.

    Args:
        token: Bearer token from the Authorization header.

    Returns:
        CurrentUser with user_id, tenant_id, role, is_platform.

    Raises:
        HTTPException 401: If the token is invalid, expired, or malformed.
    """
    settings = _get_auth_settings()
    try:
        payload = decode_access_token(token, settings.auth)
    except TokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        role = UserRole(payload["role"])
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid role in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        role=role,
        is_platform=payload.get("is_platform", False),
    )


def require_role(*allowed_roles: UserRole):
    """Create a FastAPI dependency that checks if the current user has an allowed role.

    Usage:
        @router.get("/admin-only")
        async def admin_endpoint(user: CurrentUser = require_role(UserRole.PLATFORM_ADMIN)):
            ...

    Args:
        allowed_roles: One or more UserRole values that are permitted.

    Returns:
        A Depends() that resolves to CurrentUser or raises 403.
    """
    async def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not authorized for this action",
            )
        return user
    return Depends(checker)


async def resolve_tenant_id(
    user: CurrentUser = Depends(get_current_user),
    tenant_id: str | None = Query(
        None, description="Target tenant (platform roles only)"
    ),
) -> str:
    """Resolve the effective tenant_id for the current request.

    - Organization roles: always use the tenant_id from JWT (query param ignored).
    - Platform roles: use the query param if provided, otherwise JWT tenant_id.

    This ensures tenant isolation — org users cannot access other tenants'
    data, while platform roles can switch tenant context.

    Args:
        user: The authenticated user context.
        tenant_id: Optional tenant override (only effective for platform roles).

    Returns:
        The effective tenant_id to use for data queries.
    """
    if not user.is_platform_role():
        return user.tenant_id

    if tenant_id is not None:
        return tenant_id

    return user.tenant_id