#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from scan_public_release_branch_runners import (
    PULL_REQUEST_TRIGGER,
    file_at,
    remote_branch_refs,
    targets_self_hosted_runner,
    workflow_paths,
)


ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def branch_tip(ref: str) -> str:
    return git("rev-parse", ref).stdout.strip()


def discover_unsafe_branches() -> list[dict[str, Any]]:
    unsafe: list[dict[str, Any]] = []
    for ref in remote_branch_refs():
        branch = ref.removeprefix("refs/remotes/origin/")
        violations: list[str] = []
        for path in workflow_paths(ref):
            text = file_at(ref, path)
            if PULL_REQUEST_TRIGGER.search(text) and targets_self_hosted_runner(text):
                violations.append(path)
        if violations:
            unsafe.append(
                {
                    "branch": branch,
                    "tipSha": branch_tip(ref),
                    "violationCount": len(violations),
                    "workflows": sorted(violations),
                }
            )
    return sorted(unsafe, key=lambda item: item["branch"])


def write_manifest(path: Path) -> dict[str, Any]:
    unsafe = discover_unsafe_branches()
    payload = {
        "schema": "public_release.archived_unsafe_branch_tips.v1",
        "reason": "remove non-main PR base refs that can route pull_request code to self-hosted runners before public release",
        "sourceMainCommit": os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD").stdout.strip(),
        "unsafeBranchCount": len(unsafe),
        "unsafeWorkflowCount": sum(item["violationCount"] for item in unsafe),
        "branches": unsafe,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"ARCHIVED_UNSAFE_BRANCH_COUNT={payload['unsafeBranchCount']}")
    print(f"ARCHIVED_UNSAFE_WORKFLOW_COUNT={payload['unsafeWorkflowCount']}")
    return payload


def delete_from_manifest(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    branches = payload.get("branches") or []
    if payload.get("unsafeBranchCount") != len(branches):
        raise SystemExit("manifest branch count mismatch")
    if not branches:
        print("NO_UNSAFE_BRANCHES_TO_DELETE=true")
        return

    for item in branches:
        branch = str(item["branch"])
        expected_sha = str(item["tipSha"])
        if branch == "main" or branch.endswith("/main"):
            raise SystemExit(f"refusing to delete protected branch name: {branch}")
        remote_ref = f"refs/remotes/origin/{branch}"
        actual = git("rev-parse", remote_ref).stdout.strip()
        if actual != expected_sha:
            raise SystemExit(f"branch moved after archive: {branch} expected={expected_sha} actual={actual}")

    for item in branches:
        branch = str(item["branch"])
        expected_sha = str(item["tipSha"])
        print(f"DELETE_UNSAFE_BRANCH {branch} {expected_sha}")
        proc = git("push", "origin", "--delete", branch, check=False)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            raise SystemExit(f"failed to delete unsafe branch: {branch}")

    print(f"DELETED_UNSAFE_BRANCH_COUNT={len(branches)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--delete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output:
        write_manifest(args.output)
    if args.delete:
        if not args.manifest:
            raise SystemExit("--delete requires --manifest")
        delete_from_manifest(args.manifest)
    if not args.output and not args.delete:
        raise SystemExit("select --output and/or --delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
