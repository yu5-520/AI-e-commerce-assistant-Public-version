"""CLI for approved change manifests and soft-gate completeness reports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from .change_manifest import load_change_manifest
from .completeness_report import build_completeness_report, git_changed_paths

BETA1_VERSION = "23.0.0-beta.1"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a V23 beta.1 module-contract and update-completeness report."
    )
    parser.add_argument("manifest", help="Path to registry.change_manifest.v1 JSON.")
    parser.add_argument("--base-ref", help="Optional Git base ref for real changed-path detection.")
    parser.add_argument("--head-ref", default="HEAD", help="Git head ref; defaults to HEAD.")
    parser.add_argument("--output", help="Optional JSON report path inside the repository.")
    parser.add_argument(
        "--warn-exit-code",
        action="store_true",
        help="Return exit code 3 when the soft gate warns. Default remains non-blocking.",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    manifest_path = (root / args.manifest).resolve()
    if root not in manifest_path.parents:
        raise SystemExit("manifest_path_must_be_inside_repository")
    manifest = load_change_manifest(manifest_path)

    changed_paths = None
    git_evidence = None
    if args.base_ref:
        git_evidence = git_changed_paths(
            root,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
        )
        if git_evidence.get("resolved") is True:
            changed_paths = git_evidence.get("paths") or []

    report = build_completeness_report(
        manifest,
        root,
        changed_paths_override=changed_paths,
    )
    report["gitChangedPathEvidence"] = git_evidence
    report["version"] = BETA1_VERSION
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    if args.output:
        output_path = (root / args.output).resolve()
        if root not in output_path.parents:
            raise SystemExit("output_path_must_be_inside_repository")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")

    print(rendered, end="")
    if report.get("validation", {}).get("valid") is not True:
        return 2
    if args.warn_exit_code and report.get("softGatePassed") is not True:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
