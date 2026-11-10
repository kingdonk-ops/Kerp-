"""Module loader and manifest contract (T-0011).

Discovery reads ``<package>/<module>/manifest.py``, checks every module against the
manifest contract, orders the modules by dependency and fails fast, with every problem
listed, on unknown or cyclic dependencies.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.module_loader.loader import (
    API_PREFIX,
    ModuleLoadError,
    ModuleRegistry,
    discover_modules,
    order_by_dependencies,
    registry_of,
)
from app.core.module_loader.manifest import (
    REQUIRED_FILES,
    RESERVED_NAMES,
    ModuleManifest,
    manifest_problems,
)
from app.main import create_app
from tests.module_support import FakeModule, ModulePackages


@pytest.fixture
def packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModulePackages:
    return ModulePackages(tmp_path, monkeypatch)


def _m(name: str, *deps: str, toggleable: bool = True) -> ModuleManifest:
    return ModuleManifest(name=name, version="1.0.0", depends_on=deps, toggleable=toggleable)


def _load_error(package: str) -> list[str]:
    with pytest.raises(ModuleLoadError) as info:
        discover_modules(package)
    return info.value.problems


# --- the shipped modules ---------------------------------------------------------------------


def test_shipped_modules_meet_the_manifest_contract() -> None:
    # Every package under app/modules must load: manifest, files, known acyclic deps.
    registry = discover_modules()
    assert registry.package == "app.modules"
    for manifest in registry.manifests:
        assert manifest_problems(manifest, directory=manifest.name) == []


def test_required_files_match_the_engineering_rules() -> None:
    # CLAUDE.md §6: manifest.py, models.py, router.py, service.py, schemas.py, README.md.
    assert set(REQUIRED_FILES) == {
        "manifest.py",
        "models.py",
        "router.py",
        "service.py",
        "schemas.py",
        "README.md",
    }


# --- manifest contract -----------------------------------------------------------------------


def test_a_valid_manifest_has_no_problems() -> None:
    manifest = ModuleManifest(name="quality", version="0.1.0", depends_on=("assets",))
    assert manifest_problems(manifest, directory="quality") == []
    assert manifest.route_prefix == "/quality"
    assert ModuleManifest(name="field_time", version="1.0.0").route_prefix == "/field-time"


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ("not a manifest", "MANIFEST must be a ModuleManifest"),
        (ModuleManifest(name="Quality", version="1.0.0"), "name 'Quality' must match"),
        (ModuleManifest(name="q", version="1.0.0"), "name 'q' must match"),
        (ModuleManifest(name="other", version="1.0.0"), "does not match its directory 'quality'"),
        (ModuleManifest(name="quality", version="1.0"), "version '1.0' must be MAJOR.MINOR.PATCH"),
        (
            ModuleManifest(name="quality", version="1.0.0", depends_on=("quality",)),
            "depends on itself",
        ),
        (
            ModuleManifest(name="quality", version="1.0.0", depends_on=("a1", "a1")),
            "lists 'a1' twice",
        ),
        (
            ModuleManifest(name="quality", version="1.0.0", depends_on=["assets"]),
            "depends_on must be a tuple of module names",
        ),
        (
            ModuleManifest(name="quality", version="1.0.0", depends_on=(3,)),
            "depends_on must be a tuple of module names",
        ),
        (
            ModuleManifest(name="quality", version="1.0.0", toggleable="yes"),
            "toggleable must be a bool",
        ),
        (
            ModuleManifest(name="quality", version="1.0.0", default_enabled=1),
            "default_enabled must be a bool",
        ),
        (
            ModuleManifest(
                name="quality", version="1.0.0", toggleable=False, default_enabled=False
            ),
            "cannot be disabled, so default_enabled must be True",
        ),
    ],
)
def test_manifest_contract_violations(manifest: object, expected: str) -> None:
    problems = manifest_problems(manifest, directory="quality")
    assert any(expected in p for p in problems), problems


def test_reserved_names_are_refused() -> None:
    assert "modules" in RESERVED_NAMES  # /api/v1/modules is the toggle API
    manifest = ModuleManifest(name="modules", version="1.0.0")
    assert any("reserved" in p for p in manifest_problems(manifest, directory="modules"))


# --- discovery -------------------------------------------------------------------------------


def test_discovers_modules_in_dependency_order(packages: ModulePackages) -> None:
    package = packages.write(
        {
            "quality": FakeModule(depends_on=("assets", "projects")),
            "assets": FakeModule(depends_on=("projects",)),
            "projects": FakeModule(toggleable=False),
            "welding": FakeModule(depends_on=("quality",)),
        }
    )
    registry = discover_modules(package)
    assert registry.names() == ("projects", "assets", "quality", "welding")
    assert registry.get("quality").depends_on == ("assets", "projects")
    assert "welding" in registry
    assert "nope" not in registry
    assert [m.name for m in registry.dependants("assets")] == ["quality"]
    assert [m.name for m in registry.dependants("projects")] == ["assets", "quality"]
    with pytest.raises(LookupError, match="nope"):
        registry.get("nope")


def test_ignores_directories_that_are_not_packages(packages: ModulePackages) -> None:
    package = packages.write({"assets": FakeModule()})
    root = packages.root / package
    (root / "notes").mkdir()
    (root / "loose.py").write_text("", encoding="utf-8")
    assert discover_modules(package).names() == ("assets",)


def test_an_empty_package_loads_nothing(packages: ModulePackages) -> None:
    assert discover_modules(packages.write({})).manifests == ()


@pytest.mark.parametrize("missing", REQUIRED_FILES)
def test_a_module_missing_a_required_file_fails(packages: ModulePackages, missing: str) -> None:
    package = packages.write({"assets": FakeModule(omit=(missing,))})
    problems = _load_error(package)
    assert f"assets: missing required file {missing}" in problems


def test_a_manifest_without_manifest_attribute_fails(packages: ModulePackages) -> None:
    package = packages.write({"assets": FakeModule(manifest="VERSION = '1'\n")})
    assert "assets: manifest.py must define MANIFEST" in _load_error(package)


def test_a_manifest_naming_another_module_fails(packages: ModulePackages) -> None:
    package = packages.write({"assets": FakeModule(name="quality")})
    assert any("does not match its directory 'assets'" in p for p in _load_error(package))


def test_unknown_dependencies_fail_fast(packages: ModulePackages) -> None:
    package = packages.write(
        {"quality": FakeModule(depends_on=("assets", "ghost")), "assets": FakeModule()}
    )
    assert _load_error(package) == ["quality: depends on unknown module 'ghost'"]


def test_cyclic_dependencies_fail_fast(packages: ModulePackages) -> None:
    package = packages.write(
        {
            "a1": FakeModule(depends_on=("c1",)),
            "b1": FakeModule(depends_on=("a1",)),
            "c1": FakeModule(depends_on=("b1",)),
            "d1": FakeModule(),
        }
    )
    assert _load_error(package) == ["dependency cycle among modules: a1, b1, c1"]


def test_every_problem_is_reported_at_once(packages: ModulePackages) -> None:
    package = packages.write(
        {"a1": FakeModule(omit=("schemas.py",)), "b1": FakeModule(version="one")}
    )
    problems = _load_error(package)
    assert "a1: missing required file schemas.py" in problems
    assert any(p.startswith("b1: version 'one'") for p in problems)
    with pytest.raises(ModuleLoadError, match="module loading failed"):
        discover_modules(package)


def test_a_broken_manifest_import_surfaces(packages: ModulePackages) -> None:
    package = packages.write({"a1": FakeModule(manifest="import does_not_exist_kerp\n")})
    with pytest.raises(ModuleNotFoundError, match="does_not_exist_kerp"):
        discover_modules(package)


# --- ordering --------------------------------------------------------------------------------


def test_order_is_deterministic_and_stable_by_name() -> None:
    ordered = order_by_dependencies([_m("zeta"), _m("alpha"), _m("mid", "zeta")])
    assert [m.name for m in ordered] == ["alpha", "zeta", "mid"]


def test_a_cannot_disable_module_may_not_depend_on_a_toggleable_one() -> None:
    # It could otherwise be switched off through its dependency.
    with pytest.raises(ModuleLoadError) as info:
        order_by_dependencies([_m("assets"), _m("core1", "assets", toggleable=False)])
    assert info.value.problems == [
        "core1: cannot be disabled but depends on toggleable module 'assets'"
    ]


def test_duplicate_module_names_fail() -> None:
    with pytest.raises(ModuleLoadError, match="declared twice"):
        order_by_dependencies([_m("assets"), _m("assets")])


# --- mounting --------------------------------------------------------------------------------


def test_create_app_mounts_each_module_under_the_api_prefix(packages: ModulePackages) -> None:
    package = packages.write({"field_time": FakeModule(), "projects": FakeModule(toggleable=False)})
    app = create_app(Settings(environment="test"), modules_package=package)
    registry = registry_of(app)
    assert isinstance(registry, ModuleRegistry)
    assert registry.names() == ("field_time", "projects")
    paths = set(app.openapi()["paths"])
    assert f"{API_PREFIX}/field-time/ping" in paths
    assert f"{API_PREFIX}/projects/ping" in paths
    assert f"{API_PREFIX}/modules" in paths
    # A module that cannot be disabled needs no database to serve.
    with TestClient(app) as client:
        assert client.get(f"{API_PREFIX}/projects/ping").json() == {"module": "projects"}


def test_the_default_app_loads_app_modules() -> None:
    app = create_app(Settings(environment="test"))
    assert registry_of(app).package == "app.modules"


def test_a_router_module_without_router_fails(packages: ModulePackages) -> None:
    package = packages.write({"assets": FakeModule(router="ROUTES = []\n")})
    with pytest.raises(ModuleLoadError, match=r"assets: router.py must define router"):
        create_app(Settings(environment="test"), modules_package=package)


def test_registry_of_requires_a_loaded_app() -> None:
    with pytest.raises(RuntimeError, match="module registry"):
        registry_of(FastAPI())
