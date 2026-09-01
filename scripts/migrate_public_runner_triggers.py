#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def remove_pull_request_trigger(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    start = None
    end = None
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == "  pull_request:":
            start = i
            break
    if start is None:
        return text, False

    # The PR trigger is a child of top-level `on:`. Stop at the next sibling trigger.
    sibling = re.compile(r"^  [A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?(?:\r?\n)?$")
    for i in range(start + 1, len(lines)):
        if sibling.match(lines[i]):
            end = i
            break
    if end is None:
        raise RuntimeError("pull_request trigger has no following sibling trigger")

    del lines[start:end]
    return "".join(lines), True


def harden_workflow(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "self-hosted" not in text or "  pull_request:" not in text:
        return False

    updated, removed = remove_pull_request_trigger(text)
    if not removed:
        return False

    updated = updated.replace(
        "${{ github.event.pull_request.head.sha || github.sha }}",
        "${{ github.sha }}",
    )
    updated = updated.replace(
        "${{ github.event.pull_request.base.sha || inputs.baseline_commit || github.event.before || github.sha }}",
        "${{ inputs.baseline_commit || github.event.before || github.sha }}",
    )
    updated = updated.replace(
        "${{ github.event.pull_request.base.sha || github.event.before || github.sha }}",
        "${{ github.event.before || github.sha }}",
    )

    path.write_text(updated, encoding="utf-8")
    return True


def verify() -> list[str]:
    violations: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if "self-hosted" in text and "  pull_request:" in text:
            violations.append(path.relative_to(ROOT).as_posix())
    return violations


def main() -> int:
    changed: list[str] = []
    paths = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    for path in paths:
        if harden_workflow(path):
            changed.append(path.relative_to(ROOT).as_posix())

    print(f"HARDENED_WORKFLOW_COUNT={len(changed)}")
    for path in changed:
        print(f"HARDENED {path}")

    violations = verify()
    if violations:
        print("REMAINING_PUBLIC_PR_SELF_HOSTED_VIOLATIONS:", file=sys.stderr)
        for path in violations:
            print(f"- {path}", file=sys.stderr)
        return 1

    print("PUBLIC_PR_SELF_HOSTED_BOUNDARY=verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
