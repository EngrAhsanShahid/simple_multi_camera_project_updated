# shared/contracts/user.py
"""
File Use:
    Defines the User contract — user identity and camera access
    permissions within a tenant.

Implements:
    - UserRole (enum for user roles)
    - PLATFORM_ROLES / ORG_ROLES (role scope sets)
    - User (pydantic model for user documents)

Depends On:
    - pydantic

Used By:
    - shared/storage/mongo_client.py
    - shared/auth/dependencies.py
    - apps/api_service/routes/users.py
    - apps/api_service/routes/stream.py
"""

import time
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    """Role levels controlling access scope.

    Platform roles (platform_admin, platform_support) are only valid
    for users in the platform tenant. Organization roles (tenant_admin,
    operator, viewer) are only valid for organization tenants.
    """

    PLATFORM_ADMIN = "platform_admin"
    PLATFORM_SUPPORT = "platform_support"
    TENANT_ADMIN = "tenant_admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


# Role scope sets for validation
PLATFORM_ROLES: set[UserRole] = {UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_SUPPORT}
ORG_ROLES: set[UserRole] = {UserRole.TENANT_ADMIN, UserRole.OPERATOR, UserRole.VIEWER}


class User(BaseModel):
    """A user with tenant-scoped camera access permissions.

    Responsibilities:
        - Define which cameras a user can view within their tenant.
        - Carry role for authorization decisions.
        - Tenant_admin/operator roles bypass allowed_cameras (see all cameras).
        - Viewer role is filtered by allowed_cameras.
    """

    user_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    username: str
    password_hash: str = ""
    role: UserRole = UserRole.VIEWER
    allowed_cameras: list[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_login_at: float | None = None