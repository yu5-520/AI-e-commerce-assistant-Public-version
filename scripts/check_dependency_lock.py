#!/usr/bin/env python3
"""Fail closed when an installed environment differs from an exact pip lock."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable

_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")
_BOOTSTRAP_PACKAGES = {"pip", "setuptools", "wheel"}


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def iter_lock_lines(path: Path, seen: set[Path] | None = None) -> Iterable[tuple[Path, int, str]]:
    visited = seen if seen is not None else set()
    resolved = path.resolve()
    if resolved in visited:
        return
    visited.add(resolved)
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            nested = line.split(maxsplit=1)[1].strip()
            yield from iter_lock_lines((path.parent / nested).resolve(), visited)
            continue
        yield path, line_number, line


def parse_lock(path: Path) -> Dict[str, str]:
    pins: Dict[str, str] = {}
    for source, line_number, line in iter_lock_lines(path):
        match = _PIN_RE.fullmatch(line)
        if not match:
            raise ValueError(f"non_exact_lock_entry:{source}:{line_number}:{line}")
        name, version = match.groups()
        key = canonical_name(name)
        previous = pins.get(key)
        if previous and previous != version:
            raise ValueError(f"conflicting_lock_entry:{key}:{previous}:{version}")
        pins[key] = version
    if not pins:
        raise ValueError("empty_dependency_lock")
    return pins


def installed_versions() -> Dict[str, str]:
    result: Dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            result[canonical_name(name)] = distribution.version
    return result


def canonical_environment_lines(installed: Dict[str, str]) -> list[str]:
    return [
        f"{name}=={installed[name]}"
        for name in sorted(installed)
        if name not in _BOOTSTRAP_PACKAGES
    ]


def environment_hash(lines: Iterable[str]) -> str:
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def verify(lock_path: Path, *, strict_extras: bool = False) -> dict:
    pins = parse_lock(lock_path)
    installed = installed_versions()
    missing = []
    mismatched = []
    for name, expected in sorted(pins.items()):
        actual = installed.get(name)
        if actual is None:
            missing.append({"name": name, "expected": expected})
        elif actual != expected:
            mismatched.append({"name": name, "expected": expected, "actual": actual})
    extras = []
    if strict_extras:
        extras = sorted(
            name
            for name in installed
            if name not in pins and name not in _BOOTSTRAP_PACKAGES
        )
    lines = canonical_environment_lines(installed)
    return {
        "schema": "dependency.lock.verification.v1",
        "lockPath": str(lock_path.resolve()),
        "lockedPackageCount": len(pins),
        "installedPackageCount": len(lines),
        "strictExtras": strict_extras,
        "pythonVersion": platform.python_version(),
        "pipFreezeHash": environment_hash(lines),
        "missing": missing,
        "mismatched": mismatched,
        "extras": extras,
        "verified": not missing and not mismatched and not extras,
        "canonicalEnvironmentLines": lines,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", nargs="?", default="requirements.lock")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--write-freeze")
    args = parser.parse_args()
    try:
        result = verify(Path(args.lock), strict_extras=args.strict)
        if args.write_freeze:
            destination = Path(args.write_freeze)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                "\n".join(result["canonicalEnvironmentLines"]) + "\n",
                encoding="utf-8",
            )
    except Exception as exc:
        result = {
            "schema": "dependency.lock.verification.v1",
            "verified": False,
            "strictExtras": bool(args.strict),
            "errors": [str(exc)],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result.get("verified"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
