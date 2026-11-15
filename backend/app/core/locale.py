"""Validated locale value types: IANA timezone, ISO 4217 currency, BCP 47 locale tag.

Shared by the projects module (a project's settings, T-0012) and the settings module (the
deployment's defaults for new projects, T-0044), so both validate the same way:

* ``TimeZone``: an IANA zone name from the ``tzdata`` package (e.g. ``Australia/Perth``),
  not from the host's ``TZPATH``.
* ``Currency``: an active ISO 4217 code (precious metals, SDR and test codes excluded).
* ``Locale``: a BCP 47 tag ``ll[-Ssss][-RR|-nnn]``.
"""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files
from typing import Annotated, Final

from pydantic import AfterValidator

__all__ = ["ISO_4217", "Currency", "Locale", "TimeZone", "is_bcp47", "is_currency", "is_zone"]

# Active ISO 4217 codes (precious metals, SDR and test codes excluded).
_ISO_4217_CODES: Final = """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN
    BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP
    ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK
    JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK
    MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN
    PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB
    TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XCD
    XCG XOF XPF YER ZAR ZMW ZWG ZWL
"""
ISO_4217: Final = frozenset(_ISO_4217_CODES.split())
_LOCALE: Final = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$")

# In the tzdata zone list but not a real location; browsers' Intl rejects it.
_NOT_SITE_ZONES: Final = frozenset({"Factory"})


@cache
def _zones() -> frozenset[str]:
    """IANA zone names from the ``tzdata`` package, not from the host's ``TZPATH``.

    ``zoneinfo.available_timezones()`` lists every file under the host's zoneinfo directory,
    including ``localtime`` and ``posixrules``, whose meaning depends on the host.
    """
    names = (files("tzdata") / "zones").read_text(encoding="utf-8").split()
    return frozenset(names) - _NOT_SITE_ZONES


def is_zone(value: str) -> bool:
    return value in _zones()


def is_currency(value: str) -> bool:
    return value in ISO_4217


def is_bcp47(value: str) -> bool:
    return _LOCALE.match(value) is not None


def _iana(value: str) -> str:
    if not is_zone(value):
        raise ValueError(f"{value!r} is not an IANA timezone (e.g. 'Australia/Perth')")
    return value


def _currency(value: str) -> str:
    if not is_currency(value):
        raise ValueError(f"{value!r} is not an active ISO 4217 currency code (e.g. 'AUD')")
    return value


def _locale(value: str) -> str:
    if not is_bcp47(value):
        raise ValueError(f"{value!r} is not a BCP 47 locale tag (e.g. 'en-AU')")
    return value


TimeZone = Annotated[str, AfterValidator(_iana)]
Currency = Annotated[str, AfterValidator(_currency)]
Locale = Annotated[str, AfterValidator(_locale)]
