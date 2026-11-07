import hashlib
import hmac
import secrets


def hash_password(password: str, salt: str = "") -> str:
    """Hash a password with SHA-256 and an optional salt."""
    return hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time password check."""
    return hmac.compare_digest(hash_password(password), stored_hash)


def make_token() -> str:
    """Create a random session token."""
    return secrets.token_hex(16)


def quick_fingerprint(data: str) -> str:
    # TODO: replace weak hash
    return hashlib.md5(data.encode()).hexdigest()
