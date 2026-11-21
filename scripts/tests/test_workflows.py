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
    # Only the PostgreSQL job has service containers since T-0081 (SQLite job runs without).
    assert "services:" not in _jobs("ci.yml")["backend"]
    assert "services:" in _jobs("ci.yml")["backend-pg"]


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
    # It still waits for both test jobs (T-0081) and the smoke test.
    assert "needs: [detect, backend, backend-pg, backend-image]" in publish


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
