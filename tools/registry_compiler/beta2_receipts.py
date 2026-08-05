"""CLI for creating, verifying, and comparing V23 beta.2 module receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from .runtime_receipts import (
    build_runtime_receipt_set,
    compare_environment_receipts,
    load_runtime_receipt_set,
    persist_runtime_receipt_set,
    verify_runtime_receipt_set,
)

BETA2_VERSION = "23.0.0-beta.2"


def _inside(root: Path, raw: str) -> Path:
    path = (root / raw).resolve()
    if root not in path.parents:
        raise SystemExit("path_must_be_inside_repository")
    return path


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or compare V23 beta.2 gray/production module receipt sets."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create and persist one runtime receipt set.")
    create.add_argument("--environment", required=True, choices=["repository_validation", "gray", "production"])
    create.add_argument("--release-commit", required=True)
    create.add_argument("--captured-at")
    create.add_argument("--module", action="append", default=[])
    create.add_argument("--output", required=True)

    verify = sub.add_parser("verify", help="Verify one persisted receipt set against approved contracts.")
    verify.add_argument("receipt")
    verify.add_argument("--environment", choices=["repository_validation", "gray", "production"])
    verify.add_argument("--release-commit")
    verify.add_argument("--module", action="append", default=[])
    verify.add_argument("--output")
    verify.add_argument("--warn-exit-code", action="store_true")

    compare = sub.add_parser("compare", help="Compare gray and production receipt parity.")
    compare.add_argument("--gray", required=True)
    compare.add_argument("--production", required=True)
    compare.add_argument("--module", action="append", default=[])
    compare.add_argument("--output")
    compare.add_argument("--warn-exit-code", action="store_true")

    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]

    if args.command == "create":
        receipt_set = build_runtime_receipt_set(
            root,
            environment=args.environment,
            release_commit=args.release_commit,
            modules=args.module,
            captured_at=args.captured_at,
        )
        target = _inside(root, args.output)
        persist_runtime_receipt_set(receipt_set, target, root)
        print(json.dumps(receipt_set, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    if args.command == "verify":
        receipt_set = load_runtime_receipt_set(_inside(root, args.receipt))
        report = verify_runtime_receipt_set(
            receipt_set,
            root,
            expected_environment=args.environment,
            expected_release_commit=args.release_commit,
            required_modules=args.module,
        )
    else:
        gray = load_runtime_receipt_set(_inside(root, args.gray))
        production = load_runtime_receipt_set(_inside(root, args.production))
        report = compare_environment_receipts(
            gray,
            production,
            root,
            required_modules=args.module,
        )

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        target = _inside(root, args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    passed = report.get("verified") is True or report.get("softGatePassed") is True
    if args.warn_exit_code and not passed:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
