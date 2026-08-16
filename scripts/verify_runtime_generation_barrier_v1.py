#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main() -> None:
    registry = _load("config/runtime_generation_lineage_registry_v1.json")
    assert registry["schema"] == "runtime.generation_lineage.registry.v1"
    assert registry["mode"] == "fail_closed"
    assert registry["rootRegistry"] == "config/runtime_contract_lineage_registry_v1.json"

    required = {
        "runtime.generation_seq",
        "runtime.generation_hash",
        "runtime.reset_state",
        "runtime.active_data_version",
        "runtime.claim_generation_hash",
        "runtime.commit_generation_hash",
        "runtime.stale_generation_reason",
        "verification.task_set_semantic_hash",
    }
    fields = set(registry.get("fields") or {})
    missing = sorted(required - fields)
    assert not missing, f"missing registered fields: {missing}"

    policies = registry["resetPolicies"]
    ephemeral = set(policies["current_runtime_ephemeral"]["tables"])
    preserved = set()
    for name in (
        "canonical_archive",
        "semantic_cache_reusable",
        "execution_audit_archive",
        "generation_control",
    ):
        preserved.update(policies[name].get("tables") or [])
    overlap = sorted(ephemeral & preserved)
    assert not overlap, f"reset policy overlap: {overlap}"

    scope = _load("config/competition_runtime_scope.json")
    assert "config/*.json" in set(scope.get("seedGlobs") or []), (
        "precise runtime package must include generation child registry"
    )

    from src.services import system_service
    from src.services.repeatability_contract_v1_service import task_set_semantic_hash

    assert ephemeral == set(system_service.RUNTIME_TABLES), (
        "registry current_runtime_ephemeral tables drift from system_service compatibility export"
    )

    left = task_set_semantic_hash(
        tasks=[
            {
                "taskId": "A",
                "dataVersion": "DV-1",
                "productId": "P1",
                "actionFamily": "ads",
                "owner": "operator",
            }
        ]
    )
    right = task_set_semantic_hash(
        tasks=[
            {
                "taskId": "B",
                "dataVersion": "DV-2",
                "productId": "P1",
                "actionFamily": "ads",
                "owner": "operator",
            }
        ]
    )
    assert left["taskSetSemanticHash"] == right["taskSetSemanticHash"]

    patch = (ROOT / "web_demo/core/runtime-generation-reset-v1.js").read_text(
        encoding="utf-8"
    )
    assert "stopImmediatePropagation" in patch
    assert "AppRouter?.currentContext?.()?.cleanup?.()" in patch
    assert "AppRouter?.navigate?.(\"system-status\")" in patch
    assert "window.location.assign(\"/#data-check\")" in patch
    assert "AppApi.refreshAfterDataImport" not in patch
    assert "AppApi.resetRuntimeData" not in patch

    pipeline = (
        ROOT / "src/services/pipeline_live_read_model_v225_service.py"
    ).read_text(encoding="utf-8")
    assert "_empty_generation_projection" in pipeline
    assert "historicalReaderInvoked" in pipeline
    assert "crossGenerationLastGoodFallbackAllowed" in pipeline

    repeatability_e2e = (
        ROOT / "scripts/run_competition_reset_repeatability_e2e.py"
    ).read_text(encoding="utf-8")
    assert "same_process_two_clean_runs_with_deterministic_contract_fixture" in repeatability_e2e
    assert "taskSetSemanticHashStable" in repeatability_e2e
    assert "runtimeGenerationRotated" in repeatability_e2e

    result = {
        "ok": True,
        "schema": registry["schema"],
        "version": registry["version"],
        "registeredFieldCount": len(fields),
        "ephemeralTableCount": len(ephemeral),
        "preservedTableCount": len(preserved),
        "taskSetSemanticHashSample": left["taskSetSemanticHash"],
        "precisePackageRegistrySeeded": True,
        "fullResetRepeatabilityE2ERegistered": True,
        "rule": (
            "Reset scope is registry-owned; worker/reset share one generation barrier; "
            "same business task set keeps one semantic hash across run identities."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
