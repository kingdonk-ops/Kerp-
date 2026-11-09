"""Static checks for the container build (T-0004).

Docker is not needed: these parse ``Dockerfile``, ``.dockerignore`` and the hashed lock
files so a broken build recipe fails locally before CI builds the image.
"""

import re
import shlex
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

BACKEND = Path(__file__).resolve().parent.parent
DOCKERFILE = BACKEND / "Dockerfile"
DOCKERIGNORE = BACKEND / ".dockerignore"
RUNTIME_LOCK = BACKEND / "requirements.lock"
BUILD_LOCK = BACKEND / "requirements-build.lock"
PYPROJECT = BACKEND / "pyproject.toml"


def _instructions() -> list[tuple[str, str]]:
    """Dockerfile instructions as ``(KEYWORD, arguments)``, continuation lines joined."""
    logical: list[str] = []
    buffer = ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if buffer and line.startswith("#"):
            continue  # comment inside a continued instruction
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        logical.append(buffer + line)
        buffer = ""
    assert not buffer, "Dockerfile ends with a dangling line continuation"
    result = []
    for entry in logical:
        keyword, _, args = entry.partition(" ")
        result.append((keyword.upper(), args.strip()))
    return result


def _stages() -> list[list[tuple[str, str]]]:
    stages: list[list[tuple[str, str]]] = []
    for keyword, args in _instructions():
        if keyword == "FROM":
            stages.append([])
        if stages:
            stages[-1].append((keyword, args))
    return stages


def _pip_installs() -> list[str]:
    commands = []
    for keyword, args in _instructions():
        if keyword != "RUN":
            continue
        for part in re.split(r"&&|;|\|\|", args):
            if re.search(r"\bpip3?\b.*\binstall\b", part):
                commands.append(part.strip())
    return commands


def _lock_pins(path: Path) -> dict[str, tuple[Version, int]]:
    """``{name: (pinned version, hash count)}`` from a ``--generate-hashes`` lock."""
    pins: dict[str, tuple[Version, int]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash=sha256:"):
            assert current is not None, f"hash without requirement in {path.name}"
            version, count = pins[current]
            pins[current] = (version, count + 1)
            continue
        requirement = line.rstrip("\\").strip()
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([^\s;]+)", requirement)
        assert match, f"{path.name}: not an exact pin: {requirement!r}"
        current = str(canonicalize_name(match.group(1)))
        pins[current] = (Version(match.group(2)), 0)
    return pins


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


# --- Dockerfile -------------------------------------------------------------------------


def test_dockerfile_is_multi_stage() -> None:
    stages = _stages()
    assert len(stages) >= 2
    first_from = stages[0][0][1]
    assert " AS " in first_from.upper(), "build stage must be named"
    # The final stage copies the prepared environment out of an earlier stage.
    assert any(k == "COPY" and "--from=" in a for k, a in stages[-1])


def test_base_images_are_pinned_python_312_slim() -> None:
    args = dict(a.split("=", 1) for k, a in _instructions() if k == "ARG" and "=" in a)
    for stage in _stages():
        image = stage[0][1].split()[0]
        image = re.sub(r"\$\{?(\w+)\}?", lambda m: args.get(m.group(1), m.group(0)), image)
        if image in {s[0][1].split()[-1] for s in _stages()}:
            continue  # FROM <earlier stage>
        assert image.startswith("python:3.12-slim"), image
        assert ":latest" not in image


def test_every_pip_install_is_binary_only() -> None:
    installs = _pip_installs()
    assert installs, "Dockerfile installs nothing with pip"
    for command in installs:
        assert "--only-binary=:all:" in command, command


def test_requirements_are_installed_from_hashed_locks() -> None:
    installs = _pip_installs()
    for lock in (RUNTIME_LOCK.name, BUILD_LOCK.name):
        matching = [c for c in installs if lock in shlex.split(c)]
        assert matching, f"{lock} is never installed"
        for command in matching:
            assert "--require-hashes" in command, command


def test_final_stage_runs_as_numeric_non_root_user() -> None:
    final = _stages()[-1]
    users = [a for k, a in final if k == "USER"]
    assert users, "final stage must set USER"
    user = users[-1].split(":")
    assert all(part.isdigit() for part in user), f"USER must be numeric uid[:gid], got {users[-1]}"
    assert int(user[0]) != 0
    assert int(user[-1]) != 0
    # The uid is actually created in the image (otherwise tools see an anonymous user).
    created = " ".join(a for k, a in final if k == "RUN")
    assert re.search(rf"useradd\b.*--uid {user[0]}\b", created)
    # Nothing after USER switches back to root.
    last_user = max(i for i, (k, _) in enumerate(final) if k == "USER")
    assert all(k != "USER" for k, _ in final[last_user + 1 :])


def test_runtime_command_and_port() -> None:
    final = _stages()[-1]
    cmd = [a for k, a in final if k == "CMD"]
    assert cmd
    assert cmd[-1].startswith("["), "CMD must use exec form"
    assert "app.main:create_app" in cmd[-1]
    assert "--factory" in cmd[-1]
    assert any(k == "EXPOSE" and a.split("/")[0] == "8000" for k, a in final)


def test_healthcheck_hits_health_without_extra_packages() -> None:
    checks = [a for k, a in _stages()[-1] if k == "HEALTHCHECK"]
    assert len(checks) == 1
    assert "/health" in checks[0]
    assert "curl" not in checks[0]
    assert "wget" not in checks[0]


def test_no_secrets_or_env_files_copied() -> None:
    for keyword, args in _instructions():
        if keyword in {"COPY", "ADD"}:
            assert ".env" not in args, args
            words = shlex.split(args)
            if not any(w.startswith("--from=") for w in words):
                sources = [w for w in words if not w.startswith("--")][:-1]
                # Copy named paths only, never the whole build context.
                assert all(s not in {".", "./", "*"} for s in sources), args
        if keyword in {"ENV", "ARG"}:
            assert not re.search(r"(PASSWORD|SECRET|TOKEN|API_KEY)", args, re.IGNORECASE), args
    assert all(k != "ADD" for k, _ in _instructions()), "use COPY, not ADD"


# --- .dockerignore ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [".venv", "tests", "**/__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".env"],
)
def test_dockerignore_excludes(pattern: str) -> None:
    entries = {
        line.strip().rstrip("/")
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert pattern in entries


# --- lock files (reproducible build) ----------------------------------------------------


@pytest.mark.parametrize("lock", [RUNTIME_LOCK, BUILD_LOCK], ids=lambda p: p.name)
def test_lock_pins_everything_with_hashes(lock: Path) -> None:
    pins = _lock_pins(lock)
    assert pins
    for name, (_, hashes) in pins.items():
        assert hashes >= 1, f"{lock.name}: {name} has no --hash"


def test_runtime_lock_matches_pyproject_dependencies() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    pins = _lock_pins(RUNTIME_LOCK)
    for spec in project["dependencies"]:
        requirement = Requirement(spec)
        name = str(canonicalize_name(requirement.name))
        assert name in pins, f"{name} missing from {RUNTIME_LOCK.name}; regenerate it"
        version = pins[name][0]
        assert requirement.specifier.contains(version, prereleases=True), (
            f"{name}=={version} violates {requirement.specifier}; regenerate the lock"
        )


def test_runtime_lock_contains_no_dev_dependencies() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    pins = _lock_pins(RUNTIME_LOCK)
    runtime = {str(canonicalize_name(Requirement(s).name)) for s in project["dependencies"]}
    for spec in project["optional-dependencies"]["dev"]:
        name = str(canonicalize_name(Requirement(spec).name))
        if name not in runtime:
            assert name not in pins, f"dev-only {name} leaked into {RUNTIME_LOCK.name}"


def test_build_lock_satisfies_build_system() -> None:
    build_system = _pyproject()["build-system"]
    assert isinstance(build_system, dict)
    pins = _lock_pins(BUILD_LOCK)
    for spec in build_system["requires"]:
        requirement = Requirement(spec)
        name = str(canonicalize_name(requirement.name))
        assert name in pins, f"{name} missing from {BUILD_LOCK.name}; regenerate it"
        assert requirement.specifier.contains(pins[name][0], prereleases=True)


@pytest.mark.parametrize("lock", [RUNTIME_LOCK, BUILD_LOCK], ids=lambda p: p.name)
def test_lock_targets_the_image_platform(lock: Path) -> None:
    header = lock.read_text(encoding="utf-8").splitlines()[:3]
    command = " ".join(header)
    assert "--generate-hashes" in command
    assert "--python-version 3.12" in command
    assert "--python-platform x86_64-manylinux_2_28" in command
    assert "--only-binary :all:" in command
