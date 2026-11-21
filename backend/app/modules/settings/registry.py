"""The runtime settings registry: entries declared in code (T-0230, diff §4.7).

A module declares its entries in its own ``setting_defs.py`` (imported by the module loader in
every API and worker process) and reads them through ``settings.service``::

    MAX_THINGS = SettingDef(key="max_things", category="things", type="int", default=5,
                            minimum=1, maximum=50, description="...")
    register(MAX_THINGS)

* Types: ``int | float | bool | string | enum | int_list``. Numeric types need both bounds;
  for ``int_list`` the bounds apply to each item and ``max_length`` to the list.
* ``validate`` refuses a value outside the type, the bounds or the deployment cap
  (``InvalidSettingError``, the API answers 422). Nothing is ever clamped on write.
* ``cap``: a value of the deployment configuration (``app.core.config.Settings``) the entry
  can never exceed. ``effective`` applies it on read, for a cap lowered after a value was
  stored.
* **No secret is ever an entry**: ``register`` refuses a key that names a secret (password,
  secret, credential, private or access key anywhere; token, key, PIN, salt, signature, DSN or
  certificate as the last word) and any type outside the six. ``cosign_pin_token_ttl_minutes``
  is fine: it is a lifetime, not a token.
* ``add_rule``: a rule across keys, checked on the values a change would leave behind
  (``offline_grace_hours`` against ``offline_window_days``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, get_args

from app.core.config import Settings

SettingType = Literal["int", "float", "bool", "string", "enum", "int_list"]
TYPES: Final[tuple[str, ...]] = get_args(SettingType)
Number = int | float
# A value as stored and returned (JSON).
Value = bool | int | float | str | list[int]

_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SECRET_ANYWHERE: Final = (
    "secret",
    "password",
    "passwd",
    "passphrase",
    "credential",
    "private_key",
    "access_key",
)
_SECRET_LAST_WORD: Final = frozenset(
    {"token", "key", "pin", "salt", "signature", "dsn", "cert", "certificate"}
)


class SettingRegistrationError(ValueError):
    """The declaration is malformed, a duplicate, or names a secret."""


class InvalidSettingError(ValueError):
    """The value is outside the entry's type, bounds, cap or a rule (the API answers 422)."""


class UnknownSettingError(KeyError):
    """No entry is declared under this key (the API answers 404)."""


def secret_like(name: str) -> bool:
    """Whether ``name`` names a secret (so it can be neither an entry nor shown as config)."""
    lowered = name.lower()
    return any(word in lowered for word in _SECRET_ANYWHERE) or (
        lowered.rsplit("_", 1)[-1] in _SECRET_LAST_WORD
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class DeploymentCap:
    """A deployment-configuration value an entry can never exceed (``env`` is shown to admins)."""

    env: str
    limit: Callable[[Settings], Number]


@dataclass(frozen=True, slots=True, kw_only=True)
class SettingDef:
    key: str
    category: str
    type: SettingType
    default: Any
    description: str = ""
    minimum: Number | None = None
    maximum: Number | None = None
    choices: tuple[str, ...] = ()
    # Longest ``string`` (characters) or ``int_list`` (items).
    max_length: int = 255
    # Returned by the client bootstrap (T-0231) to any signed-in user.
    client_visible: bool = False
    cap: DeploymentCap | None = None
    # Extra check of a ``string`` (IANA zone, ISO 4217...): returns the value or raises ValueError.
    check: Callable[[str], str] | None = None

    def cap_value(self, config: Settings | None) -> Number | None:
        return self.cap.limit(config) if self.cap is not None and config is not None else None

    def validate(self, value: object, *, config: Settings | None = None) -> Value:
        """``value`` if this entry accepts it, else ``InvalidSettingError``."""
        checked = self._typed(value)
        cap = self.cap_value(config)
        over = cap is not None and isinstance(checked, int | float) and checked > cap
        if over and self.cap is not None:
            raise InvalidSettingError(
                f"{self.key} may be at most {cap}: the deployment caps it ({self.cap.env})"
            )
        return checked

    def effective(self, value: Value, *, config: Settings | None = None) -> Value:
        """The stored ``value`` as consumers see it: never above the deployment cap."""
        cap = self.cap_value(config)
        if cap is None or isinstance(value, bool) or not isinstance(value, int | float):
            return value
        return min(value, cap)

    def _typed(self, value: object) -> Value:
        kind = self.type
        if kind == "bool":
            if not isinstance(value, bool):
                raise self._wrong("true or false")
            return value
        if kind == "int":
            return self._number(value, whole=True)
        if kind == "float":
            return self._number(value, whole=False)
        if kind == "int_list":
            if not isinstance(value, list) or len(value) > self.max_length:
                raise self._wrong(f"a list of at most {self.max_length} whole numbers")
            return [int(self._number(item, whole=True)) for item in value]
        if not isinstance(value, str):
            raise self._wrong("text")
        if kind == "enum":
            if value not in self.choices:
                raise self._wrong("one of " + ", ".join(self.choices))
            return value
        if len(value) > self.max_length:
            raise self._wrong(f"at most {self.max_length} characters")
        if self.check is not None:
            try:
                return self.check(value)
            except ValueError as exc:
                raise InvalidSettingError(f"{self.key}: {exc}") from exc
        return value

    def _number(self, value: object, *, whole: bool) -> Number:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or (whole and not isinstance(value, int))
            or not math.isfinite(value)
        ):
            raise self._wrong("a whole number" if whole else "a number")
        low, high = self.minimum, self.maximum
        if low is None or high is None or not low <= value <= high:
            raise self._wrong(f"between {low} and {high}")
        return value

    def _wrong(self, expected: str) -> InvalidSettingError:
        return InvalidSettingError(f"{self.key} must be {expected}")


@dataclass(frozen=True, slots=True)
class SettingRule:
    """A rule across ``keys``: ``check`` gets every effective value and returns why they do
    not fit together, or ``None``."""

    keys: tuple[str, ...]
    check: Callable[[Mapping[str, Any]], str | None]


def _problem(definition: SettingDef) -> str | None:
    key = definition.key
    if not isinstance(key, str) or not _NAME.match(key):
        return f"key {key!r} must match {_NAME.pattern}"
    if secret_like(key):
        return f"{key}: a secret is never a registry entry (diff §4.7)"
    if not isinstance(definition.category, str) or not _NAME.match(definition.category):
        return f"{key}: category {definition.category!r} must match {_NAME.pattern}"
    if definition.type not in TYPES:
        return f"{key}: type {definition.type!r} must be one of {', '.join(TYPES)}"
    low, high = definition.minimum, definition.maximum
    if definition.type in ("int", "float", "int_list") and (
        low is None or high is None or low > high
    ):
        return f"{key}: a {definition.type} entry needs minimum <= maximum"
    if definition.type == "enum" and not definition.choices:
        return f"{key}: an enum entry needs choices"
    if definition.cap is not None and definition.type not in ("int", "float"):
        return f"{key}: only a number can have a deployment cap"
    try:
        definition.validate(definition.default)
    except InvalidSettingError as exc:
        return f"the default is not a valid value: {exc}"
    return None


class SettingsRegistry:
    """The declared entries, by key. ``REGISTRY`` is the process-wide one; tests build others."""

    def __init__(self) -> None:
        self._entries: dict[str, SettingDef] = {}
        self._rules: list[SettingRule] = []

    def register(self, *definitions: SettingDef) -> None:
        for definition in definitions:
            if (problem := _problem(definition)) is not None:
                raise SettingRegistrationError(problem)
            known = self._entries.get(definition.key)
            if known is not None and known != definition:
                raise SettingRegistrationError(f"{definition.key} is already declared")
            self._entries[definition.key] = definition

    def add_rule(self, rule: SettingRule) -> None:
        for key in rule.keys:
            self.get(key)
        if rule not in self._rules:
            self._rules.append(rule)

    def get(self, key: str) -> SettingDef:
        try:
            return self._entries[key]
        except KeyError:
            raise UnknownSettingError(key) from None

    def rules_for(self, keys: set[str]) -> list[SettingRule]:
        return [rule for rule in self._rules if keys.intersection(rule.keys)]

    def __iter__(self) -> Iterator[SettingDef]:
        """Every entry, by category then key."""
        return iter(sorted(self._entries.values(), key=lambda d: (d.category, d.key)))

    def __contains__(self, key: object) -> bool:
        return key in self._entries


REGISTRY: Final = SettingsRegistry()
register = REGISTRY.register
add_rule = REGISTRY.add_rule
