"""Re-resolve the backend lock files with uv (T-0033, TD-047).

``backend/requirements.lock`` is compiled from ``pyproject.toml`` and every
``backend/requirements*.in`` into the ``.lock`` of the same name, for the image platform
(CPython 3.12, ``x86_64-manylinux_2_28``), hashed, wheels only — the commands in
``backend/README.md`` ("Lock files"). ``--upgrade`` moves every pin to the newest release
its specifier allows; the weekly ``.github/workflows/lock-refresh.yml`` runs it that way and
opens one PR to ``dev`` when a lock changed.

Each lock keeps the plain (non-upgrade) command in its header, so a refresh only changes pins.
Standard library only (it runs before the backend is installed); ``uv`` must be on ``PATH``.

    python scripts/refresh_locks.py [--upgrade] [--backend backend]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLATFORM_ARGS: tuple[str, ...] = (
    "--python-version",
    "3.12",
    "--python-platform",
    "x86_64-manylinux_2_28",
    "--generate-hashes",
    "--only-binary",
    ":all:",
)

Runner = Callable[..., object]


def lock_targets(backend: Path) -> list[tuple[str, str]]:
    """``(source, lock)`` file names, relative to ``backend``."""
    targets = [("pyproject.toml", "requirements.lock")]
    targets += [(src.name, f"{src.stem}.lock") for src in sorted(backend.glob("requirements*.in"))]
    return targets


def header_command(source: str, lock: str) -> str:
    """The command a lock's header records (as documented in ``backend/README.md``)."""
    return " ".join(("uv pip compile", source, *PLATFORM_ARGS, "-o", lock))


def compile_command(source: str, lock: str, *, upgrade: bool) -> list[str]:
    command = ["uv", "pip", "compile", source, *PLATFORM_ARGS, "-o", lock]
    command += ["--custom-compile-command", header_command(source, lock)]
    if upgrade:
        command.append("--upgrade")
    return command


def main(argv: Sequence[str] | None = None, run: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", type=Path, default=ROOT / "backend")
    parser.add_argument("--upgrade", action="store_true", help="move pins to newer releases")
    args = parser.parse_args(argv)
    backend: Path = args.backend
    for source, lock in lock_targets(backend):
        command = compile_command(source, lock, upgrade=args.upgrade)
        print("+", " ".join(command), flush=True)
        run(command, cwd=backend, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
