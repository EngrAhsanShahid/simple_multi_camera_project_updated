# shared/contracts/tenant.py
"""
File Use:
    Defines the Tenant contract — organization identity, plan limits,
    and lifecycle state for multi-tenant isolation.

Implements:
    - Tenant (pydantic model for tenant documents)

Depends On:
    - pydantic

Used By:
    - shared/storage/mongo_client.py
    - shared/config/tenant_seeder.py
    - apps/api_service/routes/tenants.py
"""

import re
import time
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Tenant(BaseModel):
    """A tenant (organization) in the multi-tenant hierarchy.

    Responsibilities:
        - Define tenant identity and contact info.
        - Carry plan limits (max_cameras, max_users).
        - Track lifecycle state (active, deactivated).
        - Distinguish platform tenant via is_platform flag.
    """

    tenant_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    slug: str
    contact_email: str | None = None
    is_platform: bool = False
    is_active: bool = True
    max_cameras: int = 50
    max_users: int = 20
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    deactivated_at: float | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Slug must be lowercase alphanumeric with hyphens only."""
        if len(v) == 1:
            if not re.match(r"^[a-z0-9]$", v):
                raise ValueError("single-character slug must be lowercase alphanumeric")
        elif not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", v):
            raise ValueError(
                "slug must be lowercase alphanumeric with hyphens, "
                "cannot start or end with a hyphen"
            )
        return v