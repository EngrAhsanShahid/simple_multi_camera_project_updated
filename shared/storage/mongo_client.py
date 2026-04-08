# shared/storage/mongo_client.py
"""
File Use:
    Synchronous MongoDB client for persisting alerts, camera configs,
    users, tenants, refresh tokens, and audit logs.
    Wraps pymongo with repository-style methods for each collection.

Implements:
    - MongoAlertStore

Depends On:
    - pymongo
    - shared.config.settings
    - shared.contracts.alert_event
    - shared.contracts.camera_config
    - shared.utils.logging

Used By:
    - apps/event_processor/main.py (alert persistence callback)
    - apps/api_service (alert, camera, user, tenant queries)
    - shared/audit/logger.py (audit event persistence)
"""

import time
from typing import Any

from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database

from shared.config.settings import MongoSettings
from shared.utils.logging import get_logger


class MongoAlertStore:
    """Synchronous MongoDB store for alerts, cameras, users, tenants,
    refresh tokens, and audit logs.

    Responsibilities:
        - Connect to MongoDB using pymongo.
        - Provide insert and query methods for all collections.
        - Create indexes for efficient querying.
    """

    def __init__(self, settings: MongoSettings | None = None) -> None:
        """Initialize the MongoDB store.

        Args:
            settings: MongoDB connection settings. Uses defaults if None.
        """
        self._settings = settings or MongoSettings()
        self._logger = get_logger("mongo_store")

        # 1) Connect to MongoDB
        self._client: MongoClient = MongoClient(
            self._settings.uri,
            serverSelectionTimeoutMS=5000,
        )
        self._db: Database = self._client[self._settings.database]

        # 2) Collection references
        self._alerts: Collection = self._db["alerts"]
        self._cameras: Collection = self._db["cameras"]
        self._users: Collection = self._db["users"]
        self._tenants: Collection = self._db["tenants"]
        self._refresh_tokens: Collection = self._db["refresh_tokens"]
        self._audit_log: Collection = self._db["audit_log"]

        # 3) Ensure indexes
        self._ensure_indexes()

        self._logger.info(
            "mongo_store_connected",
            database=self._settings.database,
        )

    @property
    def db(self) -> Database:
        """Expose the database for direct collection access (e.g., audit logger)."""
        return self._db

    def _ensure_indexes(self) -> None:
        """Create indexes for efficient querying."""
        # Alerts: query by tenant + camera + time, and by alert_id
        self._alerts.create_index(
            [("tenant_id", 1), ("camera_id", 1), ("timestamp", DESCENDING)],
            background=True,
        )
        self._alerts.create_index("alert_id", unique=True, background=True)

        # Cameras: query by tenant, unique by tenant + camera_id
        self._cameras.create_index(
            [("tenant_id", 1), ("camera_id", 1)],
            unique=True,
            background=True,
        )

        # Users: unique by user_id, query by tenant
        self._users.create_index("user_id", unique=True, background=True)
        self._users.create_index(
            [("tenant_id", 1), ("username", 1)],
            unique=True,
            background=True,
        )

        # Tenants: unique by tenant_id, name, and slug
        self._tenants.create_index("tenant_id", unique=True, background=True)
        self._tenants.create_index("name", unique=True, background=True)
        self._tenants.create_index("slug", unique=True, background=True)

        # Refresh tokens: unique by jti, query by user_id
        self._refresh_tokens.create_index("jti", unique=True, background=True)
        self._refresh_tokens.create_index("user_id", background=True)
        # TTL index: auto-delete expired tokens
        self._refresh_tokens.create_index(
            "expires_at",
            expireAfterSeconds=0,
            background=True,
        )

        # Audit log: query by tenant + time, by action + time
        self._audit_log.create_index(
            [("tenant_id", 1), ("timestamp", DESCENDING)],
            background=True,
        )
        self._audit_log.create_index(
            [("action", 1), ("timestamp", DESCENDING)],
            background=True,
        )
        # TTL index: auto-delete after 90 days (7776000 seconds)
        self._audit_log.create_index(
            "timestamp",
            expireAfterSeconds=7_776_000,
            background=True,
        )

    # ─── Alert operations ───

    def insert_alert(self, alert_dict: dict[str, Any]) -> str:
        """Insert an alert document.

        Args:
            alert_dict: Alert data as a dictionary (from AlertEvent.model_dump()).

        Returns:
            The alert_id of the inserted document.
        """
        self._alerts.insert_one(alert_dict)
        alert_id = alert_dict.get("alert_id", "")
        self._logger.debug(
            "alert_inserted",
            alert_id=alert_id,
            alert_type=alert_dict.get("alert_type"),
        )
        return alert_id

    def get_alerts(
        self,
        tenant_id: str,
        camera_id: str | None = None,
        alert_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query alerts with optional filters.

        Args:
            tenant_id: Required tenant filter.
            camera_id: Optional camera filter.
            alert_type: Optional alert type filter.
            status: Optional status filter (e.g., "open", "acknowledged", "resolved").
            limit: Maximum number of results.

        Returns:
            List of alert documents, newest first.
        """
        query: dict[str, Any] = {"tenant_id": tenant_id}
        if camera_id is not None:
            query["camera_id"] = camera_id
        if alert_type is not None:
            query["alert_type"] = alert_type
        if status is not None:
            query["status"] = status

        cursor = (
            self._alerts.find(query, {"_id": 0})
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        return list(cursor)

    def update_alert_status(self, alert_id: str, status: str) -> bool:
        """Update the status of an alert.

        Args:
            alert_id: The alert's unique identifier.
            status: New status string ("open", "acknowledged", "resolved").

        Returns:
            True if the alert was found and updated.
        """
        result = self._alerts.update_one(
            {"alert_id": alert_id},
            {"$set": {"status": status}},
        )
        if result.matched_count > 0:
            self._logger.debug("alert_status_updated", alert_id=alert_id, status=status)
        return result.matched_count > 0

    def count_unresolved_alerts(self, tenant_id: str) -> int:
        """Count open + acknowledged alerts for a tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            Count of alerts with status 'open' or 'acknowledged'.
        """
        return self._alerts.count_documents({
            "tenant_id": tenant_id,
            "status": {"$in": ["open", "acknowledged"]},
        })

    def get_alert_by_id(self, alert_id: str) -> dict[str, Any] | None:
        """Retrieve a single alert by its alert_id.

        Args:
            alert_id: The unique alert identifier.

        Returns:
            Alert document or None if not found.
        """
        return self._alerts.find_one({"alert_id": alert_id}, {"_id": 0})

    def update_alert_evidence(
        self,
        alert_id: str,
        snapshot_path: str | None = None,
        clip_path: str | None = None,
    ) -> bool:
        """Add evidence paths to an existing alert document.

        Args:
            alert_id: The alert's unique identifier.
            snapshot_path: MinIO object path for the snapshot JPEG.
            clip_path: MinIO object path for the video clip MP4.

        Returns:
            True if the alert was found and updated.
        """
        update: dict[str, str] = {}
        if snapshot_path is not None:
            update["snapshot_path"] = snapshot_path
        if clip_path is not None:
            update["clip_path"] = clip_path
        if not update:
            return False

        result = self._alerts.update_one(
            {"alert_id": alert_id},
            {"$set": update},
        )
        if result.matched_count > 0:
            self._logger.debug(
                "alert_evidence_updated",
                alert_id=alert_id,
                snapshot_path=snapshot_path,
                clip_path=clip_path,
            )
        return result.matched_count > 0

    # ─── Camera operations ───

    def upsert_camera(self, camera_dict: dict[str, Any]) -> str:
        """Insert or update a camera configuration.

        Args:
            camera_dict: Camera data as a dictionary (from CameraConfig.model_dump()).

        Returns:
            The camera_id.
        """
        self._cameras.update_one(
            {
                "tenant_id": camera_dict["tenant_id"],
                "camera_id": camera_dict["camera_id"],
            },
            {"$set": camera_dict},
            upsert=True,
        )
        return camera_dict["camera_id"]

    def get_cameras(self, tenant_id: str) -> list[dict[str, Any]]:
        """List all cameras for a tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            List of camera configuration documents.
        """
        return list(self._cameras.find({"tenant_id": tenant_id}, {"_id": 0}))

    def get_all_cameras(self) -> list[dict[str, Any]]:
        """List all cameras across all tenants.

        Returns:
            List of all camera configuration documents.
        """
        return list(self._cameras.find({}, {"_id": 0}))

    def delete_camera(self, tenant_id: str, camera_id: str) -> bool:
        """Delete a camera configuration.

        Args:
            tenant_id: The tenant identifier.
            camera_id: The camera identifier.

        Returns:
            True if a document was deleted.
        """
        result = self._cameras.delete_one(
            {"tenant_id": tenant_id, "camera_id": camera_id}
        )
        return result.deleted_count > 0

    # ─── User operations ───

    def create_user(self, user_dict: dict[str, Any]) -> str:
        """Insert a user document.

        Args:
            user_dict: User data as a dictionary (from User.model_dump()).

        Returns:
            The user_id of the inserted document.
        """
        self._users.insert_one(user_dict)
        user_id = user_dict.get("user_id", "")
        self._logger.debug("user_created", user_id=user_id)
        return user_id

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """Retrieve a single user by user_id.

        Args:
            user_id: The unique user identifier.

        Returns:
            User document or None if not found.
        """
        return self._users.find_one({"user_id": user_id}, {"_id": 0})

    def get_user_by_username(
        self, tenant_id: str, username: str
    ) -> dict[str, Any] | None:
        """Retrieve a user by tenant and username.

        Args:
            tenant_id: The tenant identifier.
            username: The username to look up.

        Returns:
            User document or None if not found.
        """
        return self._users.find_one(
            {"tenant_id": tenant_id, "username": username}, {"_id": 0}
        )

    def get_users(self, tenant_id: str) -> list[dict[str, Any]]:
        """List all users for a tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            List of user documents.
        """
        return list(self._users.find({"tenant_id": tenant_id}, {"_id": 0}))

    def update_user(self, user_id: str, update_dict: dict[str, Any]) -> bool:
        """Update a user document.

        Args:
            user_id: The user's unique identifier.
            update_dict: Fields to update (e.g., role, allowed_cameras).

        Returns:
            True if the user was found and updated.
        """
        result = self._users.update_one(
            {"user_id": user_id},
            {"$set": update_dict},
        )
        if result.matched_count > 0:
            self._logger.debug("user_updated", user_id=user_id)
        return result.matched_count > 0

    def delete_user(self, user_id: str) -> bool:
        """Delete a user document.

        Args:
            user_id: The user's unique identifier.

        Returns:
            True if a document was deleted.
        """
        result = self._users.delete_one({"user_id": user_id})
        return result.deleted_count > 0

    def delete_users_by_tenant(self, tenant_id: str) -> int:
        """Delete all users belonging to a tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            Number of users deleted.
        """
        result = self._users.delete_many({"tenant_id": tenant_id})
        return result.deleted_count

    # ─── Tenant operations ───

    def create_tenant(self, tenant_dict: dict[str, Any]) -> str:
        """Insert a tenant document.

        Args:
            tenant_dict: Tenant data as a dictionary (from Tenant.model_dump()).

        Returns:
            The tenant_id of the inserted document.
        """
        self._tenants.insert_one(tenant_dict)
        tenant_id = tenant_dict.get("tenant_id", "")
        self._logger.debug("tenant_created", tenant_id=tenant_id)
        return tenant_id

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        """Retrieve a single tenant by tenant_id.

        Args:
            tenant_id: The unique tenant identifier.

        Returns:
            Tenant document or None if not found.
        """
        return self._tenants.find_one({"tenant_id": tenant_id}, {"_id": 0})

    def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Retrieve a tenant by its URL-safe slug.

        Args:
            slug: The tenant slug.

        Returns:
            Tenant document or None if not found.
        """
        return self._tenants.find_one({"slug": slug}, {"_id": 0})

    def get_tenants(self) -> list[dict[str, Any]]:
        """List all tenants.

        Returns:
            List of tenant documents.
        """
        return list(self._tenants.find({}, {"_id": 0}))

    def update_tenant(self, tenant_id: str, update_dict: dict[str, Any]) -> bool:
        """Update a tenant document.

        Args:
            tenant_id: The tenant's unique identifier.
            update_dict: Fields to update.

        Returns:
            True if the tenant was found and updated.
        """
        result = self._tenants.update_one(
            {"tenant_id": tenant_id},
            {"$set": update_dict},
        )
        if result.matched_count > 0:
            self._logger.debug("tenant_updated", tenant_id=tenant_id)
        return result.matched_count > 0

    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant document.

        Args:
            tenant_id: The tenant's unique identifier.

        Returns:
            True if a document was deleted.
        """
        result = self._tenants.delete_one({"tenant_id": tenant_id})
        return result.deleted_count > 0

    # ─── Refresh token operations ───

    def store_refresh_token(self, token_dict: dict[str, Any]) -> None:
        """Store a refresh token for tracking and revocation.

        Args:
            token_dict: Must contain jti, user_id, tenant_id, issued_at,
                        expires_at, is_revoked.
        """
        self._refresh_tokens.insert_one(token_dict)

    def get_refresh_token(self, jti: str) -> dict[str, Any] | None:
        """Retrieve a refresh token by its unique identifier.

        Args:
            jti: The JWT ID (unique token identifier).

        Returns:
            Token document or None if not found.
        """
        return self._refresh_tokens.find_one({"jti": jti}, {"_id": 0})

    def revoke_refresh_token(self, jti: str) -> bool:
        """Revoke a refresh token by marking it as revoked.

        Args:
            jti: The JWT ID to revoke.

        Returns:
            True if the token was found and revoked.
        """
        result = self._refresh_tokens.update_one(
            {"jti": jti},
            {"$set": {"is_revoked": True}},
        )
        return result.matched_count > 0

    def revoke_all_user_tokens(self, user_id: str) -> int:
        """Revoke all refresh tokens for a user (security measure).

        Args:
            user_id: The user whose tokens should be revoked.

        Returns:
            Number of tokens revoked.
        """
        result = self._refresh_tokens.update_many(
            {"user_id": user_id, "is_revoked": False},
            {"$set": {"is_revoked": True}},
        )
        return result.modified_count

    # ─── Audit log operations ───

    def insert_audit_event(self, event_dict: dict[str, Any]) -> None:
        """Insert an audit log event.

        Args:
            event_dict: Audit event data.
        """
        self._audit_log.insert_one(event_dict)

    def get_audit_events(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        user_id: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit log events with optional filters.

        Args:
            tenant_id: Filter by tenant (None = all tenants).
            action: Filter by action type.
            resource_type: Filter by resource type.
            user_id: Filter by acting user.
            from_ts: Start timestamp (inclusive).
            to_ts: End timestamp (inclusive).
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of audit event documents, newest first.
        """
        query: dict[str, Any] = {}
        if tenant_id is not None:
            query["tenant_id"] = tenant_id
        if action is not None:
            query["action"] = action
        if resource_type is not None:
            query["resource_type"] = resource_type
        if user_id is not None:
            query["user_id"] = user_id
        if from_ts is not None or to_ts is not None:
            ts_filter: dict[str, float] = {}
            if from_ts is not None:
                ts_filter["$gte"] = from_ts
            if to_ts is not None:
                ts_filter["$lte"] = to_ts
            query["timestamp"] = ts_filter

        cursor = (
            self._audit_log.find(query, {"_id": 0})
            .sort("timestamp", DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        return list(cursor)

    def delete_alerts_by_tenant(self, tenant_id: str) -> int:
        """Delete all alerts belonging to a tenant (for hard tenant deletion).

        Args:
            tenant_id: The tenant identifier.

        Returns:
            Number of alerts deleted.
        """
        result = self._alerts.delete_many({"tenant_id": tenant_id})
        return result.deleted_count

    def delete_cameras_by_tenant(self, tenant_id: str) -> int:
        """Delete all cameras belonging to a tenant (for hard tenant deletion).

        Args:
            tenant_id: The tenant identifier.

        Returns:
            Number of cameras deleted.
        """
        result = self._cameras.delete_many({"tenant_id": tenant_id})
        return result.deleted_count

    def close(self) -> None:
        """Close the MongoDB connection."""
        self._client.close()
        self._logger.info("mongo_store_closed")