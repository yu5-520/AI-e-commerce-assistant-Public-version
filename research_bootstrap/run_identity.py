from __future__ import annotations

from typing import Any, Dict

from core import sha256_json


RUN_IDENTITY_VERSION = "rb-run-id-v1"


def build_run_identity(*, experiment_id: str, case_id: str, generation_record_hash: str, condition: str, sut_commit: str, authority_policy_hash: str) -> Dict[str, Any]:
    body = {
        "version": RUN_IDENTITY_VERSION,
        "experiment_id": experiment_id,
        "case_id": case_id,
        "generation_record_hash": generation_record_hash,
        "condition": condition,
        "sut_commit": sut_commit,
        "authority_policy_hash": authority_policy_hash,
    }
    return {**body, "run_id": sha256_json(body)}


def assert_resume_compatible(existing: Dict[str, Any], requested: Dict[str, Any]) -> None:
    """Resume is legal only for the exact same immutable run identity."""
    if existing.get("run_id") != requested.get("run_id"):
        raise RuntimeError("resume_identity_mismatch")
    for key in (
        "experiment_id",
        "case_id",
        "generation_record_hash",
        "condition",
        "sut_commit",
        "authority_policy_hash",
    ):
        if existing.get(key) != requested.get(key):
            raise RuntimeError(f"resume_component_mismatch:{key}")
