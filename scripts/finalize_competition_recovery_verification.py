#!/usr/bin/env python3
"""Combine Agent-chain recovery evidence with the dedicated ERA REC-001 evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

SCHEMA = "competition.recovery_complete_verification.v1"


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def build_report(
    behavior: dict[str, Any],
    era: dict[str, Any],
) -> dict[str, Any]:
    if behavior.get("schema") != "competition.recovery_behavior_verification.v1":
        raise RuntimeError("RECOVERY_BEHAVIOR_SCHEMA_MISMATCH")
    if behavior.get("verified") is not True:
        raise RuntimeError("RECOVERY_BEHAVIOR_NOT_VERIFIED")
    if era.get("schema") != "competition.era_recovery_contract.v1":
        raise RuntimeError("ERA_RECOVERY_SCHEMA_MISMATCH")
    if era.get("verified") is not True:
        raise RuntimeError("ERA_RECOVERY_NOT_VERIFIED")

    requirements = {
        key: dict(value)
        for key, value in (behavior.get("requirements") or {}).items()
        if isinstance(value, dict)
    }
    if set(requirements) != {
        "REC-001",
        "REC-002",
        "REC-003",
        "REC-004",
        "REC-005",
        "REC-006",
        "REC-007",
        "REC-008",
    }:
        raise RuntimeError("RECOVERY_REQUIREMENT_SET_MISMATCH")

    requirements["REC-001"] = {
        "status": "behavior_verified",
        "verified": True,
        "evidenceSchema": era.get("schema"),
        "evidenceHash": era.get("eraRecoveryHash"),
        "operatingUnitCounts": [
            item.get("operatingUnitCount")
            for item in era.get("periods") or []
            if isinstance(item, dict)
        ],
        "globalProductCounts": [
            item.get("globalProductCount")
            for item in era.get("periods") or []
            if isinstance(item, dict)
        ],
        "storeCounts": [
            item.get("storeCount")
            for item in era.get("periods") or []
            if isinstance(item, dict)
        ],
        "canonicalHistoryVerified": (
            (era.get("canonicalHistory") or {}).get("verified") is True
            if isinstance(era.get("canonicalHistory"), dict)
            else False
        ),
    }

    failed = sorted(
        key for key, value in requirements.items() if value.get("verified") is not True
    )
    material = {
        "schema": SCHEMA,
        "sourceCommit": behavior.get("sourceCommit"),
        "behaviorHash": behavior.get("behaviorHash"),
        "eraRecoveryHash": era.get("eraRecoveryHash"),
        "requirements": requirements,
        "failedRequirementIds": failed,
        "motherRepairCodeMigrationRequired": bool(failed),
        "motherDeploymentImplementationMigrationAllowed": False,
        "decision": (
            "all_misrouted_repair_intents_verified_in_competition_repo"
            if not failed
            else "scoped_runtime_repair_required"
        ),
    }
    return {
        **material,
        "verified": not failed,
        "recoveryCompleteHash": digest(material),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize competition recovery evidence.")
    parser.add_argument(
        "--behavior",
        default="dist/competition-three-report-e2e/recovery-behavior-verification.json",
    )
    parser.add_argument(
        "--era",
        default="dist/competition-three-report-e2e/era-recovery-contract.json",
    )
    parser.add_argument(
        "--output",
        default="dist/competition-three-report-e2e/recovery-complete-verification.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(read_object(Path(args.behavior)), read_object(Path(args.era)))
    write_json(Path(args.output), report)
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "sourceCommit": report["sourceCommit"],
                "decision": report["decision"],
                "failedRequirementIds": report["failedRequirementIds"],
                "motherRepairCodeMigrationRequired": report[
                    "motherRepairCodeMigrationRequired"
                ],
                "recoveryCompleteHash": report["recoveryCompleteHash"],
                "requirements": {
                    key: value.get("status")
                    for key, value in report["requirements"].items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
