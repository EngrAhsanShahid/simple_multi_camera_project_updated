# shared/auth/models.py
"""
File Use:
    Authentication context models used by route dependencies.

Implements:
    - CurrentUser: Dataclass carrying authenticated user context.

Depends On:
    - shared.contracts.user

Used By:
    - shared/auth/dependencies.py
    - apps/api_service/routes/* (via Depends)
"""

from dataclasses import dataclass

from shared.contracts.user import PLATFORM_ROLES, UserRole


@dataclass(frozen=True)
class CurrentUser:
    """Authenticated user context extracted from a JWT access token.

    Carried through FastAPI request lifecycle via dependency injection.
    Provides helper methods for common authorization checks.
    """

    user_id: str
    tenant_id: str
    role: UserRole
    is_platform: bool

    def is_platform_admin(self) -> bool:
        """Check if this user is a platform admin."""
        return self.role == UserRole.PLATFORM_ADMIN

    def is_platform_role(self) -> bool:
        """Check if this user has a platform-scoped role."""
        return self.role in PLATFORM_ROLES

    def can_access_tenant(self, tenant_id: str) -> bool:
        """Check if this user can access data for the given tenant.

        Platform roles can access any tenant. Organization roles can
        only access their own tenant.
        """
        if self.is_platform_role():
            return True
        return self.tenant_id == tenant_id

    def can_manage_users(self) -> bool:
        """Check if this user can create/update/delete users."""
        return self.role in {UserRole.PLATFORM_ADMIN, UserRole.TENANT_ADMIN}