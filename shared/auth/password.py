# shared/auth/password.py
"""
File Use:
    Password hashing and verification using bcrypt directly.

Implements:
    - hash_password: Hash a plaintext password with bcrypt.
    - verify_password: Verify a plaintext password against a bcrypt hash.

Depends On:
    - bcrypt

Used By:
    - apps/api_service/routes/auth.py
    - shared/config/tenant_seeder.py
"""

import bcrypt


def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt.

    Args:
        plain: The plaintext password to hash.

    Returns:
        A bcrypt hash string (e.g., "$2b$12$...").
    """
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash.

    Args:
        plain: The plaintext password to check.
        hashed: The bcrypt hash to verify against.

    Returns:
        True if the password matches the hash.
    """
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))