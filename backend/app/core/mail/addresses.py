"""Recipient addresses: validation, the lookup hash and the masked display form (diff §4.6).

The send log and the suppression list never store an address in clear: they keep the SHA-256
of the normalised address (to look it up) and a masked form for System admins
(``a***@example.test``).
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

MAX_ADDRESS: Final = 254
# One mailbox, no display name, no header-breaking characters (CR, LF, ``<>``, ``,``, ``;``).
_ADDRESS: Final = re.compile(r"^[^@\s<>\"',;()\[\]\\]+@[^@\s<>\"',;()\[\]\\]+\.[A-Za-z0-9-]{2,}$")


class InvalidAddressError(ValueError):
    """Not a single deliverable e-mail address."""


def normalize_address(address: str) -> str:
    """Trimmed and lower-cased; ``InvalidAddressError`` for anything but one plain mailbox."""
    text = address.strip().lower()
    if not text or len(text) > MAX_ADDRESS or not _ADDRESS.match(text):
        raise InvalidAddressError("not a single e-mail address")
    return text


def address_hash(address: str) -> str:
    """The lookup key of an address (hex SHA-256 of the normalised form)."""
    return hashlib.sha256(normalize_address(address).encode()).hexdigest()


def mask_address(address: str) -> str:
    """``alice@example.test`` → ``a***@example.test``: enough for an admin to recognise it."""
    local, _, domain = normalize_address(address).partition("@")
    return f"{local[0]}***@{domain}"
