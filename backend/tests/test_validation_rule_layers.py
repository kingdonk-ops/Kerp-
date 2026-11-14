"""Rule store layers, project operations, locked rules and two-person unlock (T-0034).

ADR 0004 §2, §3, §5, Amendment B. Service level, against the migrated schema on SQLite and,
in CI, on PostgreSQL under RLS. Seed files, rules and tenants are synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.audit.service import chain_entries, verify_chain
from app.core.db import Base, Database, contract_violations, utcnow
from app.core.module_loader import service as module_settings
from app.core.module_loader.loader import ModuleRegistry
from app.core.module_loader.manifest import ModuleManifest
from app.core.module_loader.service import add_enable_listener, default_states, set_enabled
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.projects import service as projects
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectCreate
from app.modules.validation import service as rule_store
from app.modules.validation.defaults import DefaultsError, SeedRuleSet, load_defaults
from app.modules.validation.models import UNLOCK_TABLE, Rule, RuleSet, UnlockRequest
from app.modules.validation.schemas import RuleSpec
from app.modules.validation.service import (
    RuleLockedError,
    RuleNotFoundError,
    RuleStoreError,
    UnlockPermissionError,
    approve_unlock,
    audit_chain,
    copy_module_defaults,
    create_rule_set,
    delete_rule,
    get_rule_set,
    list_rule_sets,
    list_rules,
    list_unlock_requests,
    reject_unlock,
    request_unlock,
    reset_rule,
    reset_rule_set,
    run_checks,
    save_rule,
    seed_project,
    seed_tenant,
    set_rule_enabled,
    sync_system_defaults,
    sync_system_layer,
)
from tests.audit_support import migrated_database
from tests.tenancy_support import T1, T2

QD = uuid.UUID("00000000-0000-4000-8000-00000000d013")  # PER-13 Quality Director
OPS = uuid.UUID("00000000-0000-4000-8000-00000000d001")  # PER-01 Operations Management
QA = uuid.UUID("00000000-0000-4000-8000-00000000d006")  # PER-06 QA/QC Lead
CTX_T1 = TenancyContext(tenant_id=T1, user_id=QD)
CTX_T2 = TenancyContext(tenant_id=T2, user_id=QD)
GATE = "demo.submit"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def rule(key: str, expression: str = "fields.ok == true", **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "rule_key": key,
        "engine": "cel",
        "expression": expression,
        "action": "block",
    }
    values.update(overrides)
    return values


def seed(
    version: int = 1,
    rules: list[dict[str, Any]] | None = None,
    *,
    key: str = GATE,
    module: str = "projects",
    kind: str = "check",
    **extra: Any,
) -> SeedRuleSet:
    if rules is None:
        rules = [
            rule("calibration", "fields.calibrated == true", locked=True, label="Calibration"),
            rule("photo", "fields.photos > 0", action="warn", severity="warning"),
        ]
    body = {"module": module, "key": key, "kind": kind, "version": version, "rules": rules}
    return SeedRuleSet.model_validate({**body, **extra})


def new_project(db: Database, code: str = "SYN-1", ctx: TenancyContext = CTX_T1) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        create = ProjectCreate.model_validate({"code": code, "name": f"Synthetic {code}"})
        return projects.create_project(s, create, actor_id=ctx.user_id).id


def in_project(project_id: uuid.UUID, user: uuid.UUID = QA) -> TenancyContext:
    return TenancyContext(tenant_id=T1, project_id=project_id, user_id=user)


def set_of(db: Database, ctx: TenancyContext, layer: str, key: str = GATE) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return next(r.id for r in list_rule_sets(s, layer) if r.key == key)


def rules_of(db: Database, ctx: TenancyContext, set_id: uuid.UUID) -> dict[str, Rule]:
    with db.unit_of_work(ctx) as s:
        rule_set = s.get(RuleSet, set_id)
        assert rule_set is not None
        found = list_rules(s, rule_set)
        s.expunge_all()
    return {r.rule_key: r for r in found}


def events(db: Database, ctx: TenancyContext, set_id: uuid.UUID) -> list[str]:
    with db.unit_of_work(ctx) as s:
        rule_set = s.get(RuleSet, set_id)
        assert rule_set is not None
        chain = audit_chain(rule_set)
        assert verify_chain(s, chain).ok
        return [e.payload["event"] for e in chain_entries(s, chain)]


def seeded(db: Database, *seeds: SeedRuleSet) -> None:
    with db.unit_of_work(CTX_T1) as s:
        seed_tenant(s, seeds or (seed(),))


def spec(key: str, expression: str = "fields.ok == true", **overrides: Any) -> RuleSpec:
    return RuleSpec.model_validate(rule(key, expression, **overrides))


# --- contract --------------------------------------------------------------------------------


def test_the_unlock_table_meets_the_record_contract_and_is_under_rls() -> None:
    assert UNLOCK_TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(UNLOCK_TABLE)] == []
    assert UNLOCK_TABLE in rls_tables(Base.metadata)


# --- seed files (ADR 0004 §2.1) ---------------------------------------------------------------


def write(root: Path, module: str, name: str, text: str) -> None:
    folder = root / module / "rules" / "defaults"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


SEED_YAML = """
key: {key}
kind: check
version: 2
rules:
  - rule_key: calibration
    engine: cel
    expression: "fields.calibrated == true"
    action: block
    locked: true
    label: Calibration valid
    params: {{message: "Calibration expired"}}
"""


def test_seed_files_load_per_module_sorted_by_key(tmp_path: Path) -> None:
    write(tmp_path, "quality", "gates.yaml", SEED_YAML.format(key="quality.submit"))
    write(tmp_path, "assets", "gates.yaml", SEED_YAML.format(key="assets.commit"))
    write(tmp_path, "assets", "notes.txt", "not a seed file")
    loaded = load_defaults(tmp_path)
    assert [(s.module, s.key, s.version) for s in loaded] == [
        ("assets", "assets.commit", 2),
        ("quality", "quality.submit", 2),
    ]
    calibration = loaded[0].rules[0]
    assert (calibration.locked, calibration.label) == (True, "Calibration valid")
    assert calibration.params == {"message": "Calibration expired"}


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("key: [unclosed", "not valid YAML"),
        ("- a list", "one mapping"),
        ("key: x.y\nkind: verify\nversion: 1", "kind must be one of"),
        ("key: x.y\nkind: check\nversion: 0", "greater than or equal to 1"),
        ("key: x.y\nkind: check\nversion: 1\nextra: 1", "Extra inputs"),
        (
            "key: x.y\nkind: check\nversion: 1\nrules:\n"
            "  - {rule_key: a, engine: cel, expression: 'nope.x', action: block}",
            "rule 'a': CEL UNDECLARED_REFERENCE",
        ),
        (
            "key: x.y\nkind: check\nversion: 1\nrules:\n"
            "  - {rule_key: a, engine: cel, expression: 'true', action: block}\n"
            "  - {rule_key: a, engine: cel, expression: 'true', action: block}",
            "rule_key repeated",
        ),
        (
            "key: x.y\nkind: check\nversion: 1\nrules:\n"
            "  - {rule_key: a, engine: cel, expression: 'true', action: block,"
            " origin_rule_key: a}",
            "no origin_rule_key",
        ),
    ],
)
def test_broken_seed_files_are_refused_with_the_file_named(
    tmp_path: Path, text: str, error: str
) -> None:
    write(tmp_path, "quality", "broken.yaml", text)
    with pytest.raises(DefaultsError, match=error) as caught:
        load_defaults(tmp_path)
    assert "broken.yaml" in str(caught.value)


def test_a_rule_set_key_belongs_to_one_module(tmp_path: Path) -> None:
    write(tmp_path, "quality", "a.yaml", SEED_YAML.format(key="shared.gate"))
    write(tmp_path, "assets", "b.yaml", SEED_YAML.format(key="shared.gate"))
    with pytest.raises(DefaultsError, match="defined by both assets and quality"):
        load_defaults(tmp_path)


def test_the_shipped_seed_files_load_and_compile() -> None:
    for loaded in load_defaults():
        assert loaded.rules, f"{loaded.key} ships no rules"


# --- system layer: per-tenant copies of the seed files ------------------------------------------


def test_the_system_layer_is_loaded_per_tenant_and_reloading_is_idempotent(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        assert sync_system_layer(s, [seed()]) == [GATE]
    set_id = set_of(db, CTX_T1, "system")
    with db.unit_of_work(CTX_T1) as s:
        assert sync_system_layer(s, [seed()]) == []  # same version: nothing written
        system_set = s.get(RuleSet, set_id)
        assert system_set is not None
        assert (system_set.tenant_id, system_set.project_id) == (T1, None)
        assert (system_set.module, system_set.seed_version, system_set.version) == (
            "projects",
            1,
            1,
        )
    assert events(db, CTX_T1, set_id) == ["system_defaults_loaded"]
    assert sorted(rules_of(db, CTX_T1, set_id)) == ["calibration", "photo"]


def test_tenant_b_cannot_read_tenant_as_system_rows(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        sync_system_layer(s, [seed()])
    set_id = set_of(db, CTX_T1, "system")
    with db.unit_of_work(CTX_T2) as s:
        assert list_rule_sets(s, "system") == []
        assert s.scalars(select(RuleSet)).all() == []
        assert s.scalars(select(Rule)).all() == []
        with pytest.raises(OutOfScopeError):
            get_rule_set(s, set_id, "system")


def test_a_new_seed_version_reaches_every_tenants_system_layer(db: Database) -> None:
    assert sync_system_defaults(db, [T1, T2], [seed()]) == {T1: [GATE], T2: [GATE]}
    upgraded = seed(
        2,
        [
            rule("calibration", "fields.calibrated == true && fields.cert != ''", locked=True),
            rule("witness", "fields.witnessed == true"),
        ],
    )
    assert sync_system_defaults(db, [T1, T2], [upgraded]) == {T1: [GATE], T2: [GATE]}
    assert sync_system_defaults(db, [T1, T2], [upgraded]) == {T1: [], T2: []}
    for ctx in (CTX_T1, CTX_T2):
        set_id = set_of(db, ctx, "system")
        found = rules_of(db, ctx, set_id)
        assert "cert" in found["calibration"].expression
        assert found["calibration"].version == 2
        assert found["photo"].deleted_at is not None
        assert found["witness"].version == 1
        assert events(db, ctx, set_id) == ["system_defaults_loaded", "system_defaults_upgraded"]
    # A removed default that comes back is restored, not refused.
    with db.unit_of_work(CTX_T1) as s:
        assert sync_system_layer(s, [seed(3)]) == [GATE]
    assert rules_of(db, CTX_T1, set_of(db, CTX_T1, "system"))["photo"].deleted_at is None


def test_an_older_seed_version_is_ignored_and_the_kind_is_fixed(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        sync_system_layer(s, [seed(5)])
        assert sync_system_layer(s, [seed(4, [rule("other")])]) == []
        with pytest.raises(RuleStoreError, match="kind is fixed"):
            sync_system_layer(s, [seed(6, [rule("d", action="include")], kind="digest")])


def test_the_layers_need_a_tenant_level_session(db: Database) -> None:
    project = new_project(db)
    with db.unit_of_work(in_project(project)) as s:
        with pytest.raises(RuleStoreError, match="tenant-level session"):
            sync_system_layer(s, [seed()])
        with pytest.raises(RuleStoreError, match="tenant-level session"):
            seed_project(s, project)
    with db.unit_of_work(CTX_T1) as s, pytest.raises(RuntimeError, match="project-bound"):
        copy_module_defaults(s, ["projects"])


# --- tenant seed and project copies (ADR 0004 §2.2-2.3) -----------------------------------------


def test_creating_a_tenant_seeds_the_tenant_layer_from_the_system_defaults(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    system = rules_of(db, CTX_T1, set_of(db, CTX_T1, "system"))
    copied = rules_of(db, CTX_T1, tenant_set)
    assert sorted(copied) == ["calibration", "photo"]
    calibration = copied["calibration"]
    assert (calibration.origin_rule_key, calibration.origin_version) == ("calibration", 1)
    assert calibration.locked
    assert calibration.label == "Calibration"
    assert calibration.id != system["calibration"].id
    assert events(db, CTX_T1, tenant_set) == ["rule_set_copied"]
    with db.unit_of_work(CTX_T1) as s:
        assert seed_tenant(s, [seed()]) == []  # idempotent


def test_creating_a_project_copies_the_tenant_layer_and_later_changes_do_not_leak(
    db: Database,
) -> None:
    seeded(db)
    project = new_project(db)
    project_set = set_of(db, in_project(project), "project")
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:  # PER-13 tightens the company default afterwards
        save_rule(s, tenant_set, spec("photo", "fields.photos > 2", action="warn"))
        save_rule(s, tenant_set, spec("extra", "fields.extra == true"))
    copied = rules_of(db, in_project(project), project_set)
    assert sorted(copied) == ["calibration", "photo"]
    assert copied["photo"].expression == "fields.photos > 0"
    assert copied["photo"].origin_version == 1
    # The tenant layer itself is unchanged by the project's existence; the copy is used.
    with db.unit_of_work(in_project(project)) as s:
        result = run_checks({"fields": {"calibrated": True, "photos": 1}}, GATE, session=s)
    assert (result.passed, result.evaluated) == (True, 2)
    # A project created now gets the latest defaults.
    later = new_project(db, "SYN-2")
    assert sorted(rules_of(db, in_project(later), set_of(db, in_project(later), "project"))) == [
        "calibration",
        "extra",
        "photo",
    ]


def test_a_module_off_by_default_is_copied_when_first_enabled(db: Database) -> None:
    seeded(db, seed(), seed(key="welding.submit", module="welding_demo"))
    project = new_project(db)
    with db.unit_of_work(in_project(project)) as s:
        assert [r.key for r in list_rule_sets(s, "project")] == [GATE]
    registry = ModuleRegistry.from_manifests(
        "synthetic",
        [
            ModuleManifest(name="projects", version="1.0.0", toggleable=False),
            ModuleManifest(name="welding_demo", version="1.0.0", default_enabled=False),
        ],
    )
    assert default_states(registry) == {"projects": True, "welding_demo": False}
    with db.unit_of_work(in_project(project)) as s:
        set_enabled(s, registry, "welding_demo", True)
    with db.unit_of_work(in_project(project)) as s:
        assert [r.key for r in list_rule_sets(s, "project")] == [GATE, "welding.submit"]
        set_enabled(s, registry, "welding_demo", False)
    tenant_set = set_of(db, CTX_T1, "tenant", "welding.submit")
    with db.unit_of_work(CTX_T1) as s:
        delete_rule(s, tenant_set, "photo")
    with db.unit_of_work(in_project(project)) as s:  # re-enabling keeps the existing copy
        set_enabled(s, registry, "welding_demo", True)
        copied = set_of(db, in_project(project), "project", "welding.submit")
    assert sorted(rules_of(db, in_project(project), copied)) == ["calibration", "photo"]


def test_enable_listeners_register_once(db: Database) -> None:
    calls: list[frozenset[str]] = []

    def listener(_session: object, modules: frozenset[str]) -> None:
        calls.append(modules)

    add_enable_listener(listener)
    add_enable_listener(listener)
    assert module_settings._ENABLE_LISTENERS.count(listener) == 1
    registry = ModuleRegistry.from_manifests(
        "synthetic",
        [
            ModuleManifest(name="base_demo", version="1.0.0", default_enabled=False),
            ModuleManifest(name="child_demo", version="1.0.0", depends_on=("base_demo",)),
        ],
    )
    project = new_project(db)
    try:
        with db.unit_of_work(in_project(project)) as s:
            set_enabled(s, registry, "base_demo", True)  # child follows its dependency on
    finally:
        module_settings._ENABLE_LISTENERS.remove(listener)
    assert calls == [frozenset({"base_demo", "child_demo"})]


def test_only_the_chosen_client_packs_sets_are_copied(db: Database) -> None:
    seeded(
        db,
        seed(),
        seed(key="pack.alpha", source_pack="alpha"),
        seed(key="pack.beta", source_pack="beta"),
    )
    project = new_project(db)  # no pack chosen: base sets only
    with db.unit_of_work(in_project(project)) as s:
        assert [r.key for r in list_rule_sets(s, "project")] == [GATE]
    other = uuid.uuid4()
    with db.unit_of_work(CTX_T1) as s:
        assert seed_project(s, other, source_pack="alpha") == [GATE, "pack.alpha"]


def test_seeding_a_project_keeps_the_callers_objects_attached(db: Database) -> None:
    seeded(db)
    with db.unit_of_work(CTX_T1) as s:
        create = ProjectCreate.model_validate({"code": "SYN-9", "name": "Synthetic 9"})
        project = projects.create_project(s, create)
        assert project in s
        project.name = "Renamed in the same unit of work"
    with db.unit_of_work(CTX_T1) as s:
        assert projects.get_project(s, project.id).name == "Renamed in the same unit of work"


def test_a_failing_copy_rolls_the_project_back(db: Database) -> None:
    seeded(db)

    def boom(_session: object, _project: object) -> None:
        raise RuntimeError("copy failed")

    projects.add_created_listener(boom)
    try:
        with pytest.raises(RuntimeError, match="copy failed"), db.unit_of_work(CTX_T1) as s:
            projects.create_project(s, ProjectCreate.model_validate({"code": "X", "name": "X"}))
    finally:
        projects._CREATED_LISTENERS.remove(boom)
    with db.unit_of_work(CTX_T1) as s:
        assert s.scalars(select(Project).where(Project.code == "X")).first() is None
        assert s.scalars(select(RuleSet).where(RuleSet.layer == "project")).all() == []


def test_a_failing_copy_restores_the_tenant_scope(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded(db)

    def fail(*_args: object) -> list[str]:
        raise RuntimeError("copy failed")

    monkeypatch.setattr(rule_store, "_copy_to_project", fail)
    with db.unit_of_work(CTX_T1) as s:
        tenant_set = s.get(RuleSet, set_of(db, CTX_T1, "tenant"))
        with pytest.raises(RuntimeError, match="copy failed"):
            seed_project(s, uuid.uuid4())
        assert rule_store.session_tenancy(s) == CTX_T1
        assert tenant_set in s
        s.rollback()


def test_a_failed_transaction_still_gets_its_tenant_scope_back(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded(db)
    real_bind = rule_store.bind_tenancy

    def fail(*_args: object) -> list[str]:
        raise RuntimeError("copy failed")

    def bind(session: Session, context: TenancyContext | None) -> None:
        if context == CTX_T1:  # the restore: the connection refuses (failed transaction)
            raise SQLAlchemyError("current transaction is aborted")
        real_bind(session, context)

    monkeypatch.setattr(rule_store, "_copy_to_project", fail)
    monkeypatch.setattr(rule_store, "bind_tenancy", bind)
    with db.unit_of_work(CTX_T1) as s:
        with pytest.raises(RuntimeError, match="copy failed"):
            seed_project(s, uuid.uuid4())
        assert rule_store.session_tenancy(s) == CTX_T1
        s.rollback()


# --- project operations (ADR 0004 §3) -------------------------------------------------------


@pytest.fixture
def project(db: Database) -> tuple[uuid.UUID, uuid.UUID]:
    """A project with its copy of the default ``demo.submit`` set: (project id, set id)."""
    seeded(db)
    project_id = new_project(db)
    return project_id, set_of(db, in_project(project_id), "project")


def test_a_project_edits_disables_deletes_and_adds_rules(
    db: Database, project: tuple[uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id = project
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        edited = spec(
            "photo",
            "fields.photos >= params.min",
            params={"min": 3},
            action="warn",
            severity="info",
            label="At least three photos",
            sort_order=5,
        )
        assert save_rule(s, set_id, edited).version == 2
        assert set_rule_enabled(s, set_id, "photo", False).version == 3
        assert set_rule_enabled(s, set_id, "photo", False).version == 3  # no-op
        assert set_rule_enabled(s, set_id, "photo", True).version == 4
        added = save_rule(s, set_id, spec("site_extra", "fields.site_ok == true"))
        assert (added.origin_rule_key, added.version) == (None, 1)
        assert delete_rule(s, set_id, "site_extra").deleted_at is not None
    found = rules_of(db, ctx, set_id)
    photo = found["photo"]
    assert (photo.expression, photo.params, photo.severity, photo.label, photo.sort_order) == (
        "fields.photos >= params.min",
        {"min": 3},
        "info",
        "At least three photos",
        5,
    )
    assert (photo.origin_rule_key, photo.origin_version) == ("photo", 1)  # origin kept
    assert found["site_extra"].deleted_at is not None
    with db.unit_of_work(ctx) as s, pytest.raises(RuleStoreError, match="cannot be reused"):
        save_rule(s, set_id, spec("site_extra"))
    assert events(db, ctx, set_id) == [
        "rule_set_copied",
        "rule_saved",
        "rule_disabled",
        "rule_enabled",
        "rule_saved",
        "rule_deleted",
    ]
    with db.unit_of_work(ctx) as s:
        assert s.get(RuleSet, set_id).version == 6  # type: ignore[union-attr]


def test_an_added_rule_cannot_claim_an_origin(
    db: Database, project: tuple[uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id = project
    with (
        db.unit_of_work(in_project(project_id)) as s,
        pytest.raises(RuleStoreError, match="origin"),
    ):
        save_rule(s, set_id, spec("forged", origin_rule_key="calibration", origin_version=1))


def test_reset_one_rule_to_the_current_default(
    db: Database, project: tuple[uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id = project
    ctx = in_project(project_id)
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(ctx) as s:
        save_rule(s, set_id, spec("photo", "fields.photos > 9", action="warn"))
        save_rule(s, set_id, spec("mine"))
        delete_rule(s, set_id, "mine")
    with db.unit_of_work(CTX_T1) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 1", action="warn"))
        save_rule(s, tenant_set, spec("fresh", "fields.fresh == true"))
    with db.unit_of_work(ctx) as s:
        reset = reset_rule(s, set_id, "photo")
        assert (reset.expression, reset.origin_version, reset.version) == (
            "fields.photos > 1",
            2,
            3,
        )
        assert reset_rule(s, set_id, "photo").version == 3  # already the default: no-op
        assert reset_rule(s, set_id, "fresh").origin_rule_key == "fresh"  # a newer default
        with pytest.raises(RuleStoreError, match="added on this layer"):
            reset_rule(s, set_id, "mine")
        with pytest.raises(RuleNotFoundError, match="has no rule 'ghost'"):
            reset_rule(s, set_id, "ghost")
    assert events(db, ctx, set_id)[-2:] == ["rule_reset", "rule_reset"]


def test_reset_a_deleted_default_brings_it_back(
    db: Database, project: tuple[uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id = project
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        delete_rule(s, set_id, "photo")
    with db.unit_of_work(ctx) as s:
        restored = reset_rule(s, set_id, "photo")
        assert (restored.deleted_at, restored.version) == (None, 3)


def test_reset_the_whole_set(db: Database, project: tuple[uuid.UUID, uuid.UUID]) -> None:
    project_id, set_id = project
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        set_rule_enabled(s, set_id, "photo", False)
        save_rule(s, set_id, spec("mine"))
    with db.unit_of_work(ctx) as s:
        reset_rule_set(s, set_id)
    found = rules_of(db, ctx, set_id)
    assert found["photo"].enabled
    assert found["mine"].deleted_at is not None  # key kept
    assert found["calibration"].version == 1  # untouched: already equal to the default
    assert events(db, ctx, set_id)[-2:] == ["rule_reset", "rule_deleted"]


def test_reset_needs_a_default_layer(db: Database, project: tuple[uuid.UUID, uuid.UUID]) -> None:
    project_id, _ = project
    system_set = set_of(db, CTX_T1, "system")
    with (
        db.unit_of_work(CTX_T1) as s,
        pytest.raises(RuleStoreError, match="system layer is the default"),
    ):
        reset_rule_set(s, system_set)
    with db.unit_of_work(in_project(project_id)) as s:
        own = create_rule_set(s, key="project.only", kind="check", layer="project")
        with pytest.raises(RuleStoreError, match="no tenant default set"):
            reset_rule_set(s, own.id)


def test_the_tenant_layer_resets_to_the_system_default(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 5", action="warn"))
        assert reset_rule(s, tenant_set, "photo").expression == "fields.photos > 0"


# --- locked rules (ADR 0004 §5) --------------------------------------------------------------


def test_a_locked_rule_cannot_be_changed_disabled_or_deleted_on_the_project(
    db: Database, project: tuple[uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id = project
    ctx = in_project(project_id)
    attempts = [
        lambda s: save_rule(s, set_id, spec("calibration", "true", locked=True)),
        lambda s: save_rule(s, set_id, spec("calibration", "true")),
        lambda s: set_rule_enabled(s, set_id, "calibration", False),
        lambda s: delete_rule(s, set_id, "calibration"),
    ]
    for attempt in attempts:
        with db.unit_of_work(ctx) as s, pytest.raises(RuleLockedError, match="tenant layer"):
            attempt(s)
    with db.unit_of_work(ctx) as s:  # a stricter rule can still be added to the same set
        save_rule(s, set_id, spec("stricter", "fields.calibrated == true && fields.cert != ''"))
        with pytest.raises(RuleLockedError, match="only default rules are locked"):
            save_rule(s, set_id, spec("own_lock", locked=True))
    assert rules_of(db, ctx, set_id)["calibration"].version == 1


def test_a_locked_tenant_rule_needs_an_unlock_first(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        with pytest.raises(RuleLockedError, match="two-person unlock"):
            save_rule(s, tenant_set, spec("calibration", "true"))
        with pytest.raises(RuleLockedError):
            delete_rule(s, tenant_set, "calibration")
        locked = save_rule(s, tenant_set, spec("photo", "fields.photos > 0", locked=True))
        assert locked.locked  # the tenant layer may lock a default


# --- two-person unlock (ADR 0004 §5, Amendment B, TD-116) ----------------------------------------


def test_unlocking_needs_a_second_different_person(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        request = request_unlock(
            s, tenant_set, "calibration", adr_reference=" ADR 0099 ", reason="new standard"
        )
        request_id = request.id
        assert (request.adr_reference, request.status, request.requested_by) == (
            "ADR 0099",
            "pending",
            QD,
        )
    with (
        db.unit_of_work(CTX_T1) as s,
        pytest.raises(UnlockPermissionError, match="second, different person"),
    ):
        approve_unlock(s, request_id, actor_id=QD)
    assert rules_of(db, CTX_T1, tenant_set)["calibration"].locked
    with db.unit_of_work(CTX_T1) as s:
        unlocked = approve_unlock(s, request_id, actor_id=OPS)
        assert (unlocked.locked, unlocked.unlock_adr_reference, unlocked.version) == (
            False,
            "ADR 0099",
            2,
        )
    with db.unit_of_work(CTX_T1) as s:
        done = s.get(UnlockRequest, request_id)
        assert done is not None
        assert (done.status, done.decided_by) == ("approved", OPS)
        with pytest.raises(RuleStoreError, match="already approved"):
            approve_unlock(s, request_id, actor_id=OPS)
        save_rule(s, tenant_set, spec("calibration", "fields.calibrated == true || false"))
    assert events(db, CTX_T1, tenant_set)[-4:] == [
        "unlock_requested",
        "unlock_approved",
        "rule_unlocked",
        "rule_saved",
    ]
    with db.unit_of_work(CTX_T1) as s:
        entries = chain_entries(s, f"validation.rule_set:{tenant_set}")
        unlocked_entry = next(e for e in entries if e.payload["event"] == "rule_unlocked")
        assert unlocked_entry.payload["requested_by"] == str(QD)
        assert unlocked_entry.payload["approved_by"] == str(OPS)
        assert unlocked_entry.created_by == OPS


def test_the_database_refuses_a_self_approved_unlock(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        request_id = request_unlock(s, tenant_set, "calibration", adr_reference="ADR 1").id

    def self_approve() -> None:
        with db.unit_of_work(CTX_T1) as s:
            row = s.get(UnlockRequest, request_id)
            assert row is not None
            row.status, row.decided_by = "approved", QD
            row.decided_at = utcnow()

    with pytest.raises(IntegrityError):
        self_approve()


def test_unlock_requests_are_checked(db: Database, project: tuple[uuid.UUID, uuid.UUID]) -> None:
    project_id, project_set = project
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        with pytest.raises(RuleStoreError, match="ADR reference"):
            request_unlock(s, tenant_set, "calibration", adr_reference="  ")
        with pytest.raises(RuleStoreError, match="not locked"):
            request_unlock(s, tenant_set, "photo", adr_reference="ADR 1")
        with pytest.raises(RuleStoreError, match="tenant layer only"):
            request_unlock(s, set_of(db, CTX_T1, "system"), "calibration", adr_reference="A")
        request_unlock(s, tenant_set, "calibration", adr_reference="ADR 1")
        with pytest.raises(RuleStoreError, match="already has a pending"):
            request_unlock(s, tenant_set, "calibration", adr_reference="ADR 2")
    with (
        db.unit_of_work(TenancyContext(tenant_id=T1)) as s,
        pytest.raises(RuleStoreError, match="requesting user"),
    ):
        request_unlock(s, tenant_set, "calibration", adr_reference="ADR 3")
    with (
        db.unit_of_work(in_project(project_id)) as s,
        pytest.raises(RuleStoreError, match="tenant-level session"),
    ):
        request_unlock(s, project_set, "calibration", adr_reference="ADR 1")


def test_a_request_is_rejected_cancelled_or_outdated(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        first = request_unlock(s, tenant_set, "calibration", adr_reference="ADR 1").id
    with db.unit_of_work(CTX_T1) as s:
        assert reject_unlock(s, first, actor_id=OPS).status == "rejected"
        second = request_unlock(s, tenant_set, "calibration", adr_reference="ADR 2").id
    with db.unit_of_work(CTX_T1) as s:
        assert reject_unlock(s, second, actor_id=QD).status == "cancelled"
        third = request_unlock(s, tenant_set, "calibration", adr_reference="ADR 3").id
    with db.unit_of_work(CTX_T1) as s:  # the rule changes after the request
        locked = s.scalars(
            select(Rule).where(Rule.rule_set_id == tenant_set, Rule.rule_key == "calibration")
        ).one()
        locked.version += 1
    with db.unit_of_work(CTX_T1) as s, pytest.raises(RuleStoreError, match="changed since"):
        approve_unlock(s, third, actor_id=OPS)
    with db.unit_of_work(CTX_T1) as s:
        assert [r.status for r in list_unlock_requests(s)] == ["rejected", "cancelled", "pending"]
        assert [r.id for r in list_unlock_requests(s, "pending")] == [third]
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:  # no user: nobody decides
        with pytest.raises(UnlockPermissionError):
            reject_unlock(s, third)
        with pytest.raises(UnlockPermissionError):
            approve_unlock(s, third)
    with (
        db.unit_of_work(TenancyContext(tenant_id=T2, user_id=OPS)) as s,
        pytest.raises(OutOfScopeError),
    ):
        approve_unlock(s, third)
    assert events(db, CTX_T1, tenant_set)[-5:] == [
        "unlock_requested",
        "unlock_rejected",
        "unlock_requested",
        "unlock_cancelled",
        "unlock_requested",
    ]


def test_after_an_unlock_a_project_picks_the_change_up_by_reset(
    db: Database, project: tuple[uuid.UUID, uuid.UUID]
) -> None:
    project_id, project_set = project
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        request_id = request_unlock(s, tenant_set, "calibration", adr_reference="ADR 7").id
    with db.unit_of_work(CTX_T1) as s:
        approve_unlock(s, request_id, actor_id=OPS)
    ctx = in_project(project_id)
    assert rules_of(db, ctx, project_set)["calibration"].locked  # the copy is unchanged
    with db.unit_of_work(ctx) as s:
        reset = reset_rule(s, project_set, "calibration")
        assert (reset.locked, reset.unlock_adr_reference) == (False, "ADR 7")
        assert set_rule_enabled(s, project_set, "calibration", False).enabled is False
