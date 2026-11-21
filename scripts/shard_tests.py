"""Deterministic split of the backend test files into CI shards (T-0086, TD-512).

The ``backend-pg`` job in ``.github/workflows/ci.yml`` is a matrix of shards; each shard runs
``pytest`` on the files this script assigns to it. The split is by test **file** (fixtures and
the per-worker template databases stay per process, see ``backend/tests/pg_databases.py``):

* the files are ``backend/tests/**/test_*.py`` and ``**/*_test.py`` (pytest's default
  ``python_files``; ``scripts/tests/test_shard_tests.py`` checks the backend does not change it);
* each file weighs its measured PostgreSQL seconds from ``scripts/shard_weights.json`` (JUnit
  times plus a per-test overhead, see ``weights_from_junit``); a file
  not listed there (new since the last rebalance) weighs the mean of the listed ones;
* files are placed heaviest first (ties by path) on the lightest shard (ties by shard number):
  the same tree and weights always give the same split, every file lands in exactly one shard.

Sub-commands (paths are relative to ``backend/``, where pytest runs)::

    files --shard K --of N        the files of shard K (1-based), one per line
    check --of N                  every file in exactly one shard, no shard empty
    verify-collection --of N      ``pytest --collect-only`` (in backend/) of the whole suite equals
                                  the disjoint union of the N shards' collections (node ids)
    coverage-floor                the ``--cov-fail-under`` of backend/pyproject.toml addopts, the
                                  one floor every run enforces (backend-pg: on the combined data)
    weights JUNIT.xml...          print new shard_weights.json from the shards' JUnit reports
                                  (``-o junit_family=xunit1``, so each test case names its file)

Standard library only: the tracker job runs ``scripts/tests`` with pytest alone.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
WEIGHTS = Path(__file__).resolve().parent / "shard_weights.json"


def discover(backend: Path = BACKEND) -> list[str]:
    """Every pytest test file under ``backend/tests``, as ``tests/...`` posix paths, sorted."""
    tests = backend / "tests"
    found = {
        p.relative_to(backend).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for p in tests.rglob(pattern)
        if p.is_file()
    }
    return sorted(found)


def load_weights(path: Path = WEIGHTS) -> dict[str, float]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in data.items()}


def split(files: Sequence[str], shards: int, weights: Mapping[str, float]) -> list[list[str]]:
    """Partition ``files`` into ``shards`` lists, balanced by weight; deterministic."""
    if shards < 1:
        raise ValueError("need at least one shard")
    if len(set(files)) != len(files):
        raise ValueError("duplicate test files")
    known = [weights[f] for f in files if f in weights]
    default = sum(known) / len(known) if known else 1.0
    loads = [0.0] * shards
    out: list[list[str]] = [[] for _ in range(shards)]
    for f in sorted(files, key=lambda f: (-weights.get(f, default), f)):
        target = min(range(shards), key=lambda i: (loads[i], i))
        out[target].append(f)
        loads[target] += weights.get(f, default)
    return [sorted(s) for s in out]


def problems(files: Sequence[str], shards: Sequence[Sequence[str]]) -> list[str]:
    """Why ``shards`` is not a partition of ``files`` into non-empty parts (empty = it is)."""
    found: list[str] = []
    seen: dict[str, int] = {}
    for n, shard in enumerate(shards, start=1):
        if not shard:
            found.append(f"shard {n} is empty")
        for f in shard:
            if f in seen:
                found.append(f"{f} is in shard {seen[f]} and shard {n}")
            seen.setdefault(f, n)
    expected = set(files)
    found += [f"{f} is in no shard" for f in sorted(expected - set(seen))]
    found += [f"{f} is not a test file" for f in sorted(set(seen) - expected)]
    return found


def collection_problems(full: Iterable[str], shards: Sequence[Iterable[str]]) -> list[str]:
    """Why the shards' collected node ids are not a disjoint cover of the full collection."""
    full_set = set(full)
    found: list[str] = []
    owner: dict[str, int] = {}
    for n, ids in enumerate(shards, start=1):
        for node in ids:
            if node in owner:
                found.append(f"{node} collected by shard {owner[node]} and shard {n}")
            owner.setdefault(node, n)
    found += [f"{node} collected by no shard" for node in sorted(full_set - set(owner))]
    found += [f"{node} not in the full collection" for node in sorted(set(owner) - full_set)]
    return found


COLLECT_OPTS = ("--no-cov", "-p", "no:cacheprovider")


def collect_many(
    arg_lists: Sequence[Sequence[str]], cwd: Path, opts: Sequence[str] = COLLECT_OPTS
) -> list[list[str]]:
    """Node ids ``pytest --collect-only`` finds for each argument list, run concurrently in
    ``cwd``. A failing collection is fatal: a broken file must not vanish from every shard."""
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", *opts]
    procs = [
        subprocess.Popen(
            [*cmd, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for args in arg_lists
    ]
    results: list[list[str]] = []
    for args, proc in zip(arg_lists, procs, strict=True):
        out, err = proc.communicate()
        if proc.returncode != 0:
            sys.stderr.write(out + err)
            what = list(args) or "the suite"
            raise SystemExit(f"pytest --collect-only failed ({proc.returncode}) for {what}")
        results.append([line.strip() for line in out.splitlines() if "::" in line])
    return results


def coverage_floor(pyproject: Path = BACKEND / "pyproject.toml") -> str:
    """The ``--cov-fail-under`` value of the pytest addopts (exactly one, numeric)."""
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    addopts = config["tool"]["pytest"]["ini_options"]["addopts"]
    opts = shlex.split(addopts) if isinstance(addopts, str) else list(addopts)
    pattern = re.compile(r"--cov-fail-under=(\d+(?:\.\d+)?)")
    floors = [m.group(1) for o in opts if (m := pattern.fullmatch(o))]
    if len(floors) != 1 or float(floors[0]) <= 0:
        raise SystemExit(f"{pyproject}: want one positive --cov-fail-under in addopts: {floors}")
    return floors[0]


# Worker-seconds each test costs outside its JUnit time (xdist dispatch, coverage tracing,
# reporting). Measured on CI run 44946765058: four shards with equal JUnit sums but 594 to
# 1894 tests took 84 s to 137 s on 4 workers, about 0.1 s more per test.
PER_TEST_OVERHEAD = 0.1


def weights_from_junit(paths: Iterable[Path]) -> dict[str, float]:
    """Seconds per test file: the JUnit (xunit1) times of its test cases plus
    ``PER_TEST_OVERHEAD`` per test case."""
    totals: dict[str, float] = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            file = case.get("file")
            if not file:
                raise SystemExit(f"{path}: a test case has no file (use -o junit_family=xunit1)")
            seconds = float(case.get("time") or 0.0) + PER_TEST_OVERHEAD
            totals[file] = totals.get(file, 0.0) + seconds
    return {f: round(totals[f], 2) for f in sorted(totals)}


def _plan(of: int) -> tuple[list[str], list[list[str]]]:
    files = discover()
    return files, split(files, of, load_weights())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_files = sub.add_parser("files")
    p_files.add_argument("--shard", type=int, required=True)
    p_files.add_argument("--of", type=int, required=True)
    for name in ("check", "verify-collection"):
        sub.add_parser(name).add_argument("--of", type=int, required=True)
    p_weights = sub.add_parser("weights")
    p_weights.add_argument("junit", nargs="+", type=Path)
    sub.add_parser("coverage-floor")
    args = parser.parse_args(argv)

    if args.cmd == "coverage-floor":
        print(coverage_floor())
        return 0
    if args.cmd == "weights":
        print(json.dumps(weights_from_junit(args.junit), indent=2, sort_keys=True))
        return 0
    files, shards = _plan(args.of)
    found = problems(files, shards)
    if args.cmd == "files":
        if not 1 <= args.shard <= args.of:
            parser.error(f"--shard must be in 1..{args.of}")
        if found:  # never run a partial or overlapping slice
            print("\n".join(f"::error::{p}" for p in found), file=sys.stderr)
            return 1
        print("\n".join(shards[args.shard - 1]))
        return 0
    if args.cmd == "verify-collection" and not found:
        full, *per_shard = collect_many([[], *shards], BACKEND)
        found = collection_problems(full, per_shard)
        sizes = " + ".join(str(len(ids)) for ids in per_shard)
        total = sum(map(len, per_shard))
        print(f"collected: {len(full)} in the full suite; {sizes} = {total} in the shards")
        if not full:
            found.append("the full collection is empty")
    for n, shard in enumerate(shards, start=1):
        print(f"shard {n}/{args.of}: {len(shard)} files")
    for p in found:
        print(f"::error::{p}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
