"""Canonical serialisation, content hash and chain verification (T-0006, diff §4).

Pure functions with no database access, so verification can also run over an export.

* **Canonical JSON:** keys sorted, no whitespace, UTF-8, ``ensure_ascii=False``. Only
  values that survive a PostgreSQL ``JSONB`` round trip unchanged are allowed: ``str``
  (without NUL), ``int``, ``bool``, ``None``, lists, and objects with ``str`` keys. Floats
  are rejected, because ``JSONB`` stores them as ``numeric`` and may hand back a different
  representation (``1e2`` comes back as ``100``). Put measurements in as decimal strings.
* **Content hash:** ``sha256(b"kerp-audit-ledger\\n" + canonical_json(body)).hexdigest()``.
  ``body`` holds the version ``v`` and every immutable field of the entry: ``tenant_id``,
  ``project_id``, ``chain_key``, ``seq``, ``prev_hash``, ``created_at`` (UTC, microseconds,
  ISO 8601), ``created_by``, ``source`` and ``payload``. The mutable mixin columns
  (``updated_*``, ``deleted_at``, ``revision``, ``custom_fields``) are not hashed; the
  database refuses to change them anyway.
* **Chain:** entry ``n`` stores ``prev_hash`` = ``content_hash`` of entry ``n - 1``. Entry 1
  links to ``GENESIS_HASH``. ``seq`` runs 1, 2, 3, ... without gaps.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

HASH_VERSION: Final = 1
HASH_DOMAIN: Final = b"kerp-audit-ledger\n"
GENESIS_HASH: Final = "0" * 64


class LedgerRecord(Protocol):
    """The fields of a ledger entry that verification reads."""

    @property
    def tenant_id(self) -> uuid.UUID: ...
    @property
    def project_id(self) -> uuid.UUID | None: ...
    @property
    def chain_key(self) -> str: ...
    @property
    def seq(self) -> int: ...
    @property
    def prev_hash(self) -> str: ...
    @property
    def content_hash(self) -> str: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def created_by(self) -> uuid.UUID | None: ...
    @property
    def source(self) -> str: ...
    @property
    def payload(self) -> dict[str, Any]: ...
    @property
    def deleted_at(self) -> datetime | None: ...


def _check(value: object, path: str) -> None:
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        raise TypeError(f"{path}: float values are not allowed (use a decimal string)")
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"{path}: NUL characters are not allowed")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: object keys must be strings")
            _check(key, path)
            _check(item, f"{path}.{key}")
        return
    raise TypeError(f"{path}: {type(value).__name__} is not a JSON value")


def canonical_json(value: object) -> bytes:
    """Deterministic UTF-8 JSON for hashing. Raises for values that do not round-trip."""
    _check(value, "$")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _utc_iso(value: datetime) -> str:
    # SQLite hands back naive datetimes; every ledger timestamp is written in UTC.
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat(timespec="microseconds")


def _str_or_none(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def compute_content_hash(entry: LedgerRecord) -> str:
    """SHA-256 (hex) over the canonical body of ``entry``."""
    body = {
        "v": HASH_VERSION,
        "tenant_id": str(entry.tenant_id),
        "project_id": _str_or_none(entry.project_id),
        "chain_key": entry.chain_key,
        "seq": entry.seq,
        "prev_hash": entry.prev_hash,
        "created_at": _utc_iso(entry.created_at),
        "created_by": _str_or_none(entry.created_by),
        "source": entry.source,
        "payload": entry.payload,
    }
    return hashlib.sha256(HASH_DOMAIN + canonical_json(body)).hexdigest()


def verify_entries(entries: Sequence[LedgerRecord]) -> list[str]:
    """Problems in one chain, given its entries in ``seq`` order (empty = intact).

    Detects an edited entry (hash mismatch), a removed entry (``seq`` gap), entries out of
    order or renumbered, a broken ``prev_hash`` link and a soft-deleted entry. It cannot
    detect removal of the newest entries; that needs an anchored head hash (TD-058).
    """
    problems: list[str] = []
    previous_hash, expected = GENESIS_HASH, 1
    for entry in entries:
        label = f"seq {entry.seq}"
        if entry.seq != expected:
            problems.append(f"{label}: expected seq {expected} (entry missing or out of order)")
        elif entry.prev_hash != previous_hash:
            problems.append(f"{label}: prev_hash does not match the previous entry")
        if compute_content_hash(entry) != entry.content_hash:
            problems.append(f"{label}: content_hash does not match the entry")
        if entry.deleted_at is not None:
            problems.append(f"{label}: entry is marked deleted")
        previous_hash, expected = entry.content_hash, entry.seq + 1
    return problems


@dataclass(frozen=True, slots=True)
class ChainReport:
    """Result of verifying one chain."""

    chain_key: str
    length: int
    head_hash: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems
