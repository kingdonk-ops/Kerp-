"""HTTP routes of ``validation``: none yet.

The rule store is used through ``service.py``. The Project Settings -> Rules API, with its
permissions (ADR 0004 §7), comes with the layers in T-0034; "Try it" with T-0036.
"""

from fastapi import APIRouter

router = APIRouter()
