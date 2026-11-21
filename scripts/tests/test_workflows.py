"""Static hardening checks on every workflow in .github/workflows (T-0033, TD-047/048/051).

Line-based (the tracker job installs pytest only, no YAML parser). ci.yml's own job wiring is
covered by test_ci_gate.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
FILES = sorted(WORKFLOWS.glob("*.yml"))
NAMES = [f.name for f in FILES]
SHA_PIN = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+(\.\d+)*")


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _jobs(name: str) -> dict[str, str]:
    text = _text(name).split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", text, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _run_scripts(text: str) -> list[tuple[int, str]]:
    """``(line number, line)`` of every line of every ``run:`` script."""
    lines = text.splitlines()
    found: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:- )?run: ?(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, inline = len(m.group(1)), m.group(2)
        if inline and inline not in ("|", ">-", ">", "|-"):
            found.append((i + 1, inline))
            i += 1
            continue
        i += 1
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
            found.append((i + 1, lines[i]))
            i += 1
    return found


def test_the_workflows_are_found() -> None:
    assert {"ci.yml", "promote.yml", "review-gate.yml", "lock-refresh.yml"} <= set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_every_action_is_pinned_to_a_commit_sha_with_its_version(name: str) -> None:
    uses = [line.split("uses: ", 1)[1].strip() for line in _text(name).splitlines() if "uses: " in line]
    assert uses, name
    for ref in uses:
        assert SHA_PIN.fullmatch(ref), f"{name}: {ref}"


@pytest.mark.parametrize("name", NAMES)
def test_checkout_keeps_no_token_unless_the_job_pushes(name: str) -> None:
    for job, body in _jobs(name).items():
        lines = body.splitlines()
        pushes = any(re.search(r"\bgit push\b", line) for line in lines)
        for i, line in enumerate(lines):
            if "uses: actions/checkout@" not in line:
                continue
            block = "\n".join(lines[i + 1 : i + 6])
            if not pushes:
                assert "persist-credentials: false" in block, f"{name}/{job}"


@pytest.mark.parametrize("name", NAMES)
def test_no_expression_is_interpolated_into_a_script(name: str) -> None:
    """``${{ }}`` in a ``run:`` script is template injection waiting to happen: pass every
    value through ``env:`` (PR #16 review, docs/pr-suggestions.md P9)."""
    offenders = [f"{name}:{n}: {line.strip()}" for n, line in _run_scripts(_text(name)) if "${{" in line]
    assert offenders == []


def test_the_script_scanner_finds_inline_and_block_scripts() -> None:
    text = (
        "    steps:\n"
        "      - run: echo ${{ a }}\n"
        "      - name: x\n"
        "        run: |\n"
        "          echo one\n"
        "\n"
        "          echo ${{ b }}\n"
        "        env:\n"
        "          C: ${{ c }}\n"
    )
    assert [line.strip() for _, line in _run_scripts(text) if line.strip()] == [
        "echo ${{ a }}",
        "echo one",
        "echo ${{ b }}",
    ]


def test_ci_passes_the_base_ref_through_env() -> None:
    text = _text("ci.yml")
    for line in text.splitlines():
        if "github.base_ref" in line:
            assert re.match(r"^\s+[A-Z_]+: \$\{\{ github\.base_ref \}\}$", line), line
    tracker = _jobs("ci.yml")["tracker"]
    assert 'check-ids --base "origin/$BASE_REF" --head "$HEAD_SHA"' in " ".join(tracker.split())


def _service_images() -> list[tuple[str, str]]:
    """``(service name, image)`` of every service container in ci.yml, whichever job."""
    lines = _text("ci.yml").splitlines()
    images: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        if re.match(r"^\s+image: ", line):
            key = next(k for k in reversed(lines[:i]) if not k.strip().startswith("#"))
            images.append((key.strip().rstrip(":"), line.split("image: ", 1)[1].strip()))
    return images


def test_ci_service_containers_are_postgres_and_redis_only() -> None:
    services = _service_images()
    assert {name for name, _ in services} == {"postgres", "redis"}
    # The SQLite job runs without service containers since T-0081; the PostgreSQL jobs
    # (backend-pg, and e2e since T-0027) have them.
    assert "services:" not in _jobs("ci.yml")["backend"]
    assert "services:" in _jobs("ci.yml")["backend-pg"]
    assert "services:" in _jobs("ci.yml")["e2e"]


def _services_block(job: str) -> str:
    text = _jobs("ci.yml")[job]
    return text.split("    services:\n", 1)[1].split("\n    env:\n", 1)[0]


def test_e2e_runs_on_the_same_database_and_redis_as_backend_pg() -> None:
    """T-0027: the journey runs against what the PostgreSQL suite proves (service containers
    and the init script that creates the NOSUPERUSER NOBYPASSRLS runtime role)."""
    assert _services_block("e2e") == _services_block("backend-pg")
    e2e = _jobs("ci.yml")["e2e"]
    assert "-f ../infra/postgres/initdb/20-kerp-app.sql" in e2e
    assert "KERP_E2E_DATABASE_URL: postgresql+psycopg://kerp_app:kerp_app@" in e2e
    assert "actions/setup-python@" in e2e
    assert "npx playwright test" in e2e
    assert "continue-on-error" not in e2e


def test_backend_pg_shards_keep_the_postgres_setup_and_e2e_waits_for_all_of_them() -> None:
    """T-0086: every shard is the old backend-pg job on a slice of the files (same services,
    init script, durability settings, runtime role, Redis); e2e waits for every shard (the
    matrix job's aggregate result) and for the combined coverage floor."""
    jobs = _jobs("ci.yml")
    pg = jobs["backend-pg"]
    assert "matrix:\n        shard: [" in pg
    assert "-f ../infra/postgres/initdb/20-kerp-app.sql" in pg
    assert "ALTER SYSTEM SET fsync = off" in pg
    assert "KERP_ENVIRONMENT: test" in pg
    assert "KERP_DATABASE_URL: postgresql+psycopg://kerp_app:kerp_app@localhost:5432/kerp_test" in pg
    assert "KERP_TEST_OWNER_URL: postgresql+psycopg://kerp:kerp@localhost:5432/kerp_test" in pg
    assert "KERP_REDIS_URL: redis://localhost:6379/0" in pg
    assert pg.count("persist-credentials: false") == pg.count("uses: actions/checkout@") == 1
    # The coverage job installs the same backend and runs from the same checkout layout.
    cov = jobs["backend-pg-coverage"]
    assert "persist-credentials: false" in cov and 'pip install -e ".[dev]"' in cov
    assert "working-directory: backend" in cov
    e2e = re.search(r"needs: \[([^\]]*)\]", jobs["e2e"])
    assert e2e
    assert {n.strip() for n in e2e.group(1).split(",")} == {
        "detect", "backend", "backend-pg", "backend-pg-coverage", "frontend"
    }
    assert "!contains(needs.*.result, 'failure')" in jobs["e2e"]


@pytest.mark.parametrize(
    ("service", "tag"),
    [
        pytest.param(
            "postgres",
            "postgis/postgis:16-3.4",
            marks=pytest.mark.xfail(
                strict=True,
                reason="TD-432: postgis digest not resolvable from the implementer sandbox",
            ),
        ),
        ("redis", "redis:7-alpine"),
    ],
)
def test_ci_service_images_are_pinned_by_digest(service: str, tag: str) -> None:
    images = [image for name, image in _service_images() if name == service]
    assert images, service
    for image in images:  # every definition of the service, in any job
        assert re.fullmatch(rf"{re.escape(tag)}@sha256:[0-9a-f]{{64}} # {re.escape(tag)}", image), image


def test_publish_pushes_the_smoke_tested_image_not_a_rebuild() -> None:
    jobs = _jobs("ci.yml")
    image, publish = jobs["backend-image"], jobs["backend-publish"]
    # backend-image saves exactly what it smoke-tested and hands over its id.
    assert "image-id: ${{ steps.save.outputs.image-id }}" in image
    assert image.index("Smoke test") < image.index("docker save kerp-backend:ci")
    assert "actions/upload-artifact@" in image
    assert "push: true" not in image
    # backend-publish loads it, checks the id, pushes it and checks the registry digest.
    assert "docker/build-push-action" not in publish
    assert "setup-buildx-action" not in publish
    assert "actions/download-artifact@" in publish
    assert "TESTED_ID: ${{ needs.backend-image.outputs.image-id }}" in publish
    assert "docker load" in publish
    assert '[ "$loaded" = "$TESTED_ID" ]' in publish
    assert '[ "$pushed" = "$TESTED_ID" ]' in publish
    assert "GITHUB_STEP_SUMMARY" in publish
    # It still waits for both test jobs (T-0081; backend-pg as shards + combined coverage,
    # T-0086) and the smoke test.
    assert "needs: [detect, backend, backend-pg, backend-pg-coverage, backend-image]" in publish


def test_lock_refresh_is_weekly_least_privilege_and_app_free() -> None:
    text = _text("lock-refresh.yml")
    head = text.split("\njobs:\n", 1)[0]
    assert re.search(r"schedule:\n\s+- cron: \"[\d*]+ [\d*]+ \* \* [0-6]\"", head)
    assert "workflow_dispatch:" in head
    assert "permissions: {}" in head
    [(job, body)] = _jobs("lock-refresh.yml").items()
    perms = re.search(r"permissions:\n((?:\s{6}[\w-]+: \w+.*\n)+)", body)
    assert perms
    granted = {line.split(":")[0].strip(): line.split(":")[1].split("#")[0].strip() for line in perms.group(1).splitlines()}
    assert granted == {"contents": "write", "pull-requests": "write", "actions": "write"}, job
    for ref in re.findall(r"uses: (\S+)", text):
        assert ref.startswith("actions/"), ref  # first-party actions only, no app
    assert "python scripts/refresh_locks.py --upgrade" in text
    assert "--base dev" in text
    assert "secrets." not in text  # GITHUB_TOKEN only
