# shared/config/tenant_seeder.py
"""
File Use:
    Seeds the platform tenant, demo org tenant, and their admin users
    on first startup. Ensures the system always has a platform tenant
    for administrative operations and a demo org tenant matching the
    default camera config.

Implements:
    - seed_platform_tenant
    - seed_platform_admin
    - seed_demo_tenant
    - seed_demo_tenant_admin

Depends On:
    - shared.storage.mongo_client
    - shared.contracts.tenant
    - shared.contracts.user
    - shared.utils.logging

Used By:
    - run.py (orchestrator, on startup)
    - scripts/migrate_0.6_to_0.7.py
"""

import time

from shared.contracts.tenant import Tenant
from shared.contracts.user import User, UserRole
from shared.storage.mongo_client import MongoAlertStore
from shared.utils.logging import get_logger

logger = get_logger("tenant_seeder")

PLATFORM_TENANT_ID = "platform"
PLATFORM_TENANT_NAME = "NEXA Platform"
PLATFORM_TENANT_SLUG = "nexa-platform"
PLATFORM_ADMIN_USER_ID = "platform-admin"
PLATFORM_ADMIN_USERNAME = "admin"
PLATFORM_ADMIN_DEFAULT_PASSWORD = "admin"

DEMO_TENANT_ID = "tenant_1"
DEMO_TENANT_NAME = "Tenant 1"
DEMO_TENANT_SLUG = "tenant-1"
DEMO_ADMIN_USER_ID = "tenant1-admin"
DEMO_ADMIN_USERNAME = "admin"
DEMO_ADMIN_DEFAULT_PASSWORD = "admin"


def seed_platform_tenant(mongo_store: MongoAlertStore) -> bool:
    """Create the platform tenant if it does not exist.

    Args:
        mongo_store: MongoDB store instance.

    Returns:
        True if the platform tenant was created, False if it already exists.
    """
    existing = mongo_store.get_tenant(PLATFORM_TENANT_ID)
    if existing is not None:
        logger.debug("platform_tenant_exists", tenant_id=PLATFORM_TENANT_ID)
        return False

    tenant = Tenant(
        tenant_id=PLATFORM_TENANT_ID,
        name=PLATFORM_TENANT_NAME,
        slug=PLATFORM_TENANT_SLUG,
        contact_email=None,
        is_platform=True,
        is_active=True,
        max_cameras=0,
        max_users=0,
    )
    mongo_store.create_tenant(tenant.model_dump())
    logger.info(
        "platform_tenant_created",
        tenant_id=PLATFORM_TENANT_ID,
        name=PLATFORM_TENANT_NAME,
    )
    return True


def seed_platform_admin(
    mongo_store: MongoAlertStore,
    password_hash: str,
) -> bool:
    """Create the default platform admin user if it does not exist.

    Args:
        mongo_store: MongoDB store instance.
        password_hash: Pre-hashed password (bcrypt) for the admin user.

    Returns:
        True if the admin was created, False if it already exists.
    """
    existing = mongo_store.get_user(PLATFORM_ADMIN_USER_ID)
    if existing is not None:
        logger.debug("platform_admin_exists", user_id=PLATFORM_ADMIN_USER_ID)
        return False

    now = time.time()
    user = User(
        user_id=PLATFORM_ADMIN_USER_ID,
        tenant_id=PLATFORM_TENANT_ID,
        username=PLATFORM_ADMIN_USERNAME,
        password_hash=password_hash,
        role=UserRole.PLATFORM_ADMIN,
        allowed_cameras=[],
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )
    mongo_store.create_user(user.model_dump())
    logger.warning(
        "platform_admin_created_with_default_password",
        user_id=PLATFORM_ADMIN_USER_ID,
        message="Change the default admin password immediately!",
    )
    return True


def seed_demo_tenant(mongo_store: MongoAlertStore) -> bool:
    """Create the demo organization tenant if it does not exist.

    This tenant matches the tenant_id used in the default cameras.json,
    ensuring cameras are properly associated with a real tenant record.

    Args:
        mongo_store: MongoDB store instance.

    Returns:
        True if the demo tenant was created, False if it already exists.
    """
    existing = mongo_store.get_tenant(DEMO_TENANT_ID)
    if existing is not None:
        logger.debug("demo_tenant_exists", tenant_id=DEMO_TENANT_ID)
        return False

    tenant = Tenant(
        tenant_id=DEMO_TENANT_ID,
        name=DEMO_TENANT_NAME,
        slug=DEMO_TENANT_SLUG,
        contact_email=None,
        is_platform=False,
        is_active=True,
        max_cameras=50,
        max_users=20,
    )
    mongo_store.create_tenant(tenant.model_dump())
    logger.info(
        "demo_tenant_created",
        tenant_id=DEMO_TENANT_ID,
        name=DEMO_TENANT_NAME,
        slug=DEMO_TENANT_SLUG,
    )
    return True


def seed_demo_tenant_admin(
    mongo_store: MongoAlertStore,
    password_hash: str,
) -> bool:
    """Create a tenant_admin user for the demo org tenant if it does not exist.

    Args:
        mongo_store: MongoDB store instance.
        password_hash: Pre-hashed password (bcrypt) for the admin user.

    Returns:
        True if the admin was created, False if it already exists.
    """
    existing = mongo_store.get_user(DEMO_ADMIN_USER_ID)
    if existing is not None:
        logger.debug("demo_tenant_admin_exists", user_id=DEMO_ADMIN_USER_ID)
        return False

    now = time.time()
    user = User(
        user_id=DEMO_ADMIN_USER_ID,
        tenant_id=DEMO_TENANT_ID,
        username=DEMO_ADMIN_USERNAME,
        password_hash=password_hash,
        role=UserRole.TENANT_ADMIN,
        allowed_cameras=[],
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )
    mongo_store.create_user(user.model_dump())
    logger.info(
        "demo_tenant_admin_created",
        user_id=DEMO_ADMIN_USER_ID,
        tenant_id=DEMO_TENANT_ID,
        message="Change the default password immediately!",
    )
    return True