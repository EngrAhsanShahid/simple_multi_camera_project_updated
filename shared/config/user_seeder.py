# shared/config/user_seeder.py
"""
File Use:
    Seeds a default tenant_admin user on startup if no users exist for
    the default tenant. Ensures the system is usable immediately
    without manual user creation.

Implements:
    - seed_default_admin

Depends On:
    - shared.storage.mongo_client
    - shared.contracts.user
    - shared.utils.logging

Used By:
    - run.py (orchestrator, on startup)
"""

import time

from shared.contracts.user import User, UserRole
from shared.storage.mongo_client import MongoAlertStore
from shared.utils.logging import get_logger

logger = get_logger("user_seeder")

DEFAULT_TENANT_ID = "default"
DEFAULT_ADMIN_USER_ID = "default_admin"
DEFAULT_ADMIN_USERNAME = "admin"


def seed_default_admin(mongo_store: MongoAlertStore) -> bool:
    """Create a default tenant_admin user if none exists for the default tenant.

    Args:
        mongo_store: MongoDB store instance with user operations.

    Returns:
        True if a default admin was created, False if one already exists.
    """
    existing = mongo_store.get_user(DEFAULT_ADMIN_USER_ID)
    if existing is not None:
        logger.debug("default_admin_exists", user_id=DEFAULT_ADMIN_USER_ID)
        return False

    now = time.time()
    user = User(
        user_id=DEFAULT_ADMIN_USER_ID,
        tenant_id=DEFAULT_TENANT_ID,
        username=DEFAULT_ADMIN_USERNAME,
        role=UserRole.TENANT_ADMIN,
        allowed_cameras=[],
        password_hash="",
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )
    mongo_store.create_user(user.model_dump())
    logger.info(
        "default_admin_created",
        user_id=DEFAULT_ADMIN_USER_ID,
        tenant_id=DEFAULT_TENANT_ID,
    )
    return True