"""Write the API's OpenAPI schema to a file (TD-018, T-0011).

``python -m app.openapi <path>`` builds the app with test settings (no database, no
environment-specific values) and writes its schema as JSON. The frontend's
``npm run gen:api`` runs it and generates the typed client in ``frontend/src/api/`` from it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.main import create_app


def openapi_schema() -> dict[str, Any]:
    """The schema of the app with every module of ``app.modules`` mounted."""
    return create_app(Settings(environment="test", database_url=None)).openapi()


def write_schema(path: Path) -> None:
    """Write ``openapi_schema()`` to ``path`` as stable, indented JSON."""
    text = json.dumps(openapi_schema(), indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m app.openapi <output.json>", file=sys.stderr)
        return 2
    write_schema(Path(args[0]))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
