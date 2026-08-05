"""Product post-codegen verification using the exact external Z compiler."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

from .dependency import canonical_hash, ensure_dependency, product_root


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _strings(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip().replace("\\", "/") for value in values if str(value).strip()})


def _run_external_compile(root: Path, source: Path, approval: str, output: str) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.self_update.cli",
            "--root",
            str(root),
            "compile",
            "--approval",
            approval,
            "--output",
            output,
        ],
        cwd=source,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"EXTERNAL_Z_COMPILE_FAILED:{completed.returncode}")


def verify_post_codegen(
    approval_path: str,
    *,
    base_ref: str,
    head_ref: str,
    output_directory: str | None = None,
) -> Dict[str, Any]:
    root = product_root()
    source = ensure_dependency(root)
    approval = _read(root / approval_path)
    requirement_path = str(approval.get("requirementPath") or "")
    requirement_id = Path(requirement_path).stem or "unknown"
    output_dir = root / (output_directory or f"outputs/change-transactions/{requirement_id}")
    output_dir.mkdir(parents=True, exist_ok=True)
    program_path = output_dir / "change-program.json"
    _run_external_compile(
        root,
        source,
        approval_path,
        program_path.relative_to(root).as_posix(),
    )
    program = _read(program_path)

    completed = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    changed_paths = _strings(completed.stdout.splitlines())
    allowed = set(_strings(program.get("allowedWritePaths") or []))
    governance_patterns = (
        "contracts/requirements/*.json",
        "contracts/approvals/*.json",
        "contracts/level-transactions/*.json",
        ".github/workflows/*.yml",
        ".z/*.json",
        ".z/adapter/**",
        ".z/receipts/*.json",
        ".z/tools/*.py",
        "tools/z_adapter/**",
    )
    outside = sorted(
        path
        for path in changed_paths
        if path not in allowed
        and not any(fnmatch.fnmatch(path, pattern) for pattern in governance_patterns)
    )

    test_patterns: list[str] = []
    for request in program.get("codegenRequests") or []:
        if isinstance(request, dict):
            test_patterns.extend(_strings(request.get("requiredTests") or []))
    tests = sorted(
        {
            path.relative_to(root).as_posix()
            for pattern in test_patterns
            for path in root.glob(pattern)
            if path.is_file()
        }
    )
    test_return_code = 0
    if tests:
        test_return_code = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *tests],
            cwd=root,
            check=False,
        ).returncode

    findings: list[str] = []
    if outside:
        findings.append("WRITE_SCOPE_VIOLATION")
    if test_patterns and not tests:
        findings.append("REQUIRED_TEST_PATTERN_UNRESOLVED")
    if test_return_code != 0:
        findings.append(f"TESTS_FAILED:{test_return_code}")

    material = {
        "schema": "z.ai_ecommerce.external_post_codegen_verification.v1",
        "approvalPath": approval_path,
        "requirementPath": requirement_path,
        "programHash": program.get("programHash"),
        "baseRef": base_ref,
        "headRef": head_ref,
        "changedPaths": changed_paths,
        "outsideWriteScope": outside,
        "requiredTestPatterns": sorted(set(test_patterns)),
        "executedTests": tests,
        "testReturnCode": test_return_code,
        "findings": findings,
        "passed": not findings,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "ecsMutated": False,
    }
    result = {**material, "verificationHash": canonical_hash(material)}
    receipt_path = output_dir / "post-codegen-verification.json"
    receipt_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("approval")
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    result = verify_post_codegen(
        args.approval,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        output_directory=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("passed") is True else 5


if __name__ == "__main__":
    raise SystemExit(main())
