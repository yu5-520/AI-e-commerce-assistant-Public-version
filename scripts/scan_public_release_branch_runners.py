#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys


# This scanner is intentionally branch-tip scoped: historical blobs are audited separately.
PULL_REQUEST_TRIGGER = re.compile(r"(?m)^  pull_request:\s*(?:#.*)?$")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def targets_self_hosted_runner(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("runs-on:"):
            continue
        indent = len(line) - len(stripped)
        value = stripped.split(":", 1)[1].strip()
        if "self-hosted" in value:
            return True
        if value:
            continue
        for nested in lines[index + 1 :]:
            nested_stripped = nested.lstrip()
            if not nested_stripped or nested_stripped.startswith("#"):
                continue
            nested_indent = len(nested) - len(nested_stripped)
            if nested_indent <= indent:
                break
            if "self-hosted" in nested_stripped:
                return True
    return False


def remote_branch_refs() -> list[str]:
    result = git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/remotes/origin",
    ).stdout.splitlines()
    return sorted(
        ref
        for ref in result
        if ref != "refs/remotes/origin/HEAD" and not ref.endswith("/main")
    )


def workflow_paths(ref: str) -> list[str]:
    proc = git("ls-tree", "-r", "--name-only", ref, ".github/workflows", check=False)
    if proc.returncode != 0:
        return []
    return sorted(
        path
        for path in proc.stdout.splitlines()
        if path.endswith((".yml", ".yaml"))
    )


def file_at(ref: str, path: str) -> str:
    proc = git("show", f"{ref}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else ""


def main() -> int:
    branches = remote_branch_refs()
    violations: list[tuple[str, str]] = []
    workflow_count = 0

    for ref in branches:
        branch = ref.removeprefix("refs/remotes/origin/")
        for path in workflow_paths(ref):
            workflow_count += 1
            text = file_at(ref, path)
            if PULL_REQUEST_TRIGGER.search(text) and targets_self_hosted_runner(text):
                violations.append((branch, path))

    print(f"PUBLIC_RELEASE_NON_MAIN_BRANCH_COUNT={len(branches)}")
    print(f"PUBLIC_RELEASE_BRANCH_WORKFLOW_COUNT={workflow_count}")
    if violations:
        affected = sorted({branch for branch, _ in violations})
        print(f"PUBLIC_RELEASE_UNSAFE_BRANCH_COUNT={len(affected)}")
        print(f"PUBLIC_RELEASE_UNSAFE_WORKFLOW_COUNT={len(violations)}")
        print("PUBLIC_RELEASE_BRANCH_RUNNER_VIOLATIONS:")
        for branch, path in violations:
            print(f"- {branch} :: {path}")
        return 1

    print("PUBLIC_RELEASE_BRANCH_RUNNER_BOUNDARY=verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
