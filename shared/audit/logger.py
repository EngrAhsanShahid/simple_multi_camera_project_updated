# shared/audit/logger.py
"""
File Use:
    Fire-and-forget audit logging for security-relevant actions.
    Writes audit events to MongoDB. Never blocks or raises on failure.

Implements:
    - AuditLogger

Depends On:
    - shared.storage.mongo_client
    - shared.auth.models
    - shared.utils.logging

Used By:
    - apps/api_service/routes/auth.py
    - apps/api_service/routes/tenants.py
    - apps/api_service/routes/users.py
"""

import time
from uuid import uuid4

from shared.auth.models import CurrentUser
from shared.storage.mongo_client import MongoAlertStore
from shared.utils.logging import get_logger

logger = get_logger("audit")


class AuditLogger:
    """Writes security audit events to MongoDB.

    Fire-and-forget: failures are logged via structlog but never
    block the calling operation or raise exceptions.
    """

    def __init__(self, mongo: MongoAlertStore) -> None:
        self._mongo = mongo

    def log(
        self,
        action: str,
        resource_type: str,
        user: CurrentUser | None = None,
        resource_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
        success: bool = True,
        tenant_id: str | None = None,
        username: str | None = None,
    ) -> None:
        """Record an audit event.

        Args:
            action: Action identifier (e.g., "auth.login", "user.created").
            resource_type: Type of resource (e.g., "auth", "user", "tenant").
            user: The authenticated user performing the action (None for system/pre-auth events).
            resource_id: ID of the affected resource.
            details: Additional action-specific metadata.
            ip_address: Client IP address.
            success: Whether the action succeeded.
            tenant_id: Override tenant_id (useful for pre-auth events like login).
            username: Override username (useful for pre-auth events).
        """
        try:
            event = {
                "event_id": str(uuid4()),
                "timestamp": time.time(),
                "tenant_id": tenant_id or (user.tenant_id if user else "system"),
                "user_id": user.user_id if user else None,
                "username": username or None,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "details": details or {},
                "ip_address": ip_address,
                "success": success,
            }
            self._mongo.insert_audit_event(event)
        except Exception:
            logger.warning(
                "audit_log_failed",
                action=action,
                resource_type=resource_type,
                exc_info=True,
            )