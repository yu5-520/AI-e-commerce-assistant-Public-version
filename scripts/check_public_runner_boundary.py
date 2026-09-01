#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def workflow_texts():
    paths = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    for path in paths:
        yield path, path.read_text(encoding="utf-8")


def main() -> int:
    violations = []
    permission_violations = []
    for path, text in workflow_texts():
        relative = path.relative_to(ROOT).as_posix()
        if "self-hosted" in text and "pull_request:" in text:
            violations.append(relative)
        if "self-hosted" in text and "permissions:\n  contents: read" not in text:
            permission_violations.append(relative)

    if violations:
        print("PUBLIC_PR_SELF_HOSTED_VIOLATIONS:")
        for path in violations:
            print(f"- {path}")
    if permission_violations:
        print("SELF_HOSTED_PERMISSION_VIOLATIONS:")
        for path in permission_violations:
            print(f"- {path}")

    if violations or permission_violations:
        return 1

    print("PUBLIC_RUNNER_BOUNDARY=verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
