#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PULL_REQUEST_TRIGGER = re.compile(r"(?m)^  pull_request:\s*(?:#.*)?$")


def workflow_texts():
    paths = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    for path in paths:
        yield path, path.read_text(encoding="utf-8")


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


def main() -> int:
    violations = []
    permission_violations = []
    for path, text in workflow_texts():
        relative = path.relative_to(ROOT).as_posix()
        self_hosted = targets_self_hosted_runner(text)
        if self_hosted and PULL_REQUEST_TRIGGER.search(text):
            violations.append(relative)
        if self_hosted and "permissions:\n  contents: read" not in text:
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
