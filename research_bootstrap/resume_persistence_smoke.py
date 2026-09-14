from __future__ import annotations

import json
import tempfile
from pathlib import Path

from append_store import AppendOnlyJsonlStore
from resume_persistence import ResumePersistenceError, execute_or_resume
from run_identity import build_run_identity


def identity(**overrides):
    values = {
        "experiment_id": "stage-a-resume-smoke",
        "case_id": "completion-positive-01",
        "generation_record_hash": "gen-smoke-001",
        "condition": "baseline_runtime",
        "sut_commit": "b" * 40,
        "authority_policy_hash": "policy-smoke-001",
    }
    values.update(overrides)
    return build_run_identity(**values)


def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store = AppendOnlyJsonlStore(Path(tmp) / "results.jsonl")
        original = identity()
        executions = {"count": 0}

        def execute():
            executions["count"] += 1
            return {"generated": True, "realized": True, "receipt": "smoke"}

        first = execute_or_resume(store=store, requested_identity=original, execute_fn=execute)
        resumed = execute_or_resume(
            store=store,
            requested_identity=original,
            execute_fn=execute,
            resume_from_run_id=original["run_id"],
        )

        drift_checks = {}
        drifts = {
            "generation_record_hash": identity(generation_record_hash="gen-smoke-002"),
            "condition": identity(condition="information_authority"),
            "sut_commit": identity(sut_commit="c" * 40),
            "authority_policy_hash": identity(authority_policy_hash="policy-smoke-002"),
        }
        for name, drifted in drifts.items():
            try:
                execute_or_resume(
                    store=store,
                    requested_identity=drifted,
                    execute_fn=lambda: {"unexpected": True},
                    resume_from_run_id=original["run_id"],
                )
            except ResumePersistenceError as exc:
                drift_checks[name] = {"status": "PASS", "reason": str(exc)}
            else:
                raise AssertionError(f"identity drift was not blocked: {name}")

        verification = store.verify()
        assert first["status"] == "EXECUTED"
        assert resumed["status"] == "RESUMED"
        assert resumed["executed"] is False
        assert executions["count"] == 1
        assert verification["events"] == 1

        return {
            "resume_persistence": "PASS",
            "first_execution": first["status"],
            "resume_status": resumed["status"],
            "execution_count_after_resume": executions["count"],
            "stored_event_count": verification["events"],
            "hash_chain": verification["status"],
            "identity_drift_checks": drift_checks,
            "paid_model_calls": 0,
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
