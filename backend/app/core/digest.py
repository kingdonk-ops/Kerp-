"""The one SHA-256 hex helper (T-0349).

Secrets that are only ever compared, never read back (magic-link, reset, invite and SSO
tokens, wipe secrets), are stored and carried as this digest, so a store dump signs nobody in.
"""

import hashlib


def sha256_hex(value: str) -> str:
    """The SHA-256 of ``value`` (UTF-8), as 64 lowercase hex characters."""
    return hashlib.sha256(value.encode()).hexdigest()
