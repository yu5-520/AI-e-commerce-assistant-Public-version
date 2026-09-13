from copy import deepcopy

import pytest

from scripts.compile_v26_update_locator import UpdateLocatorError, compile_plan


def registry():
    return {
        "version": "23.0.0-rc.1",
        "registryRootHash": "sha256:root",
        "modules": {
            "agent3_runtime": {
                "fieldIds": ["agent3.execution_steps"],
                "schemaIds": ["agent_input.agent3_sop.v1"],
                "implementationPaths": [
                    "src/services/agent3_sop_core_v225_service.py",
                    "src/services/agent3_runtime_v23215_service.py",
                ],
                "runner": "src.services.pipeline_agent3_sop_v225_service:run_agent3_sop_microbatch_v225",
            },
            "task_mapping": {
                "fieldIds": ["agent3.execution_steps"],
                "schemaIds": [],
                "implementationPaths": [
                    "src/services/pipeline_task_mapping_v225_service.py"
                ],
                "runner": "src.services.pipeline_task_mapping_v225_service:run_task_mapping_microbatch_v225",
            },
            "frontend_view": {
                "fieldIds": ["frontend.module_content_hash"],
                "schemaIds": [],
                "implementationPaths": ["web_demo/index.html"],
                "runner": "src.services.frontend_view_artifact_v2259_service:materialize_frontend_views_v2259",
            },
        },
    }


def policy():
    return {
        "defaultProfile": "v26_field_authority",
        "profiles": {
            "v26_field_authority": {
                "allowedRegistryModules": ["agent3_runtime", "task_mapping"],
                "allowedRuntimePathPrefixes": [
                    "src/services/agent3_sop_core_v225_service.py",
                    "src/services/agent3_runtime_v23215_service.py",
                    "src/services/pipeline_agent3_sop_v225_service.py",
                    "src/services/pipeline_task_mapping_v225_service.py",
                    "tests/test_v22_4_v269_agent3_core_bridge.py",
                    "scripts/run_competition_three_report_e2e_v269.py",
                    ".github/workflows/v26-registry-lineage-pr.yml",
                    ".github/workflows/v269-a-three-report-candidate.yml",
                ],
            }
        },
    }


def request():
    return {
        "schema": "z.update_request.v1",
        "requestId": "test",
        "policyProfile": "v26_field_authority",
        "targets": [
            {
                "targetId": "mixed",
                "failureClass": "MIXED_PROVIDER_CONTRACT_TEST_MISMATCH",
                "registryModules": ["agent3_runtime"],
                "evidencePaths": ["tests/test_v22_4_v269_agent3_core_bridge.py"],
            },
            {
                "targetId": "scheduler",
                "failureClass": "allV269SchedulerStagesObserved",
                "registryModules": ["task_mapping"],
                "evidencePaths": ["scripts/run_competition_three_report_e2e_v269.py"],
            },
        ],
    }


def test_locator_compiles_only_registry_and_policy_owned_paths():
    plan = compile_plan(registry=registry(), policy=policy(), request=request())
    assert plan["deniedRegisteredPaths"] == []
    assert plan["rules"]["filenameSimilaritySearchAllowed"] is False
    assert plan["rules"]["unplannedFileMutationAllowed"] is False
    assert plan["rules"]["scopeExpansionRequiresRecompile"] is True
    assert "src/services/agent3_sop_core_v225_service.py" in plan["editablePaths"]
    assert "src/services/pipeline_task_mapping_v225_service.py" in plan["editablePaths"]
    assert "tests/test_v22_4_v269_agent3_core_bridge.py" in plan["editablePaths"]
    assert "scripts/run_competition_three_report_e2e_v269.py" in plan["editablePaths"]
    assert ".github/workflows/v269-a-three-report-candidate.yml" in plan["requiredGates"]
    assert plan["relatedModules"] == [
        {
            "moduleId": "task_mapping" if plan["selectedModules"][0]["moduleId"] == "agent3_runtime" else "agent3_runtime",
            "sharedFieldIds": ["agent3.execution_steps"],
            "sharedSchemaIds": [],
            "sharedImplementationPaths": [],
        }
    ] or plan["relatedModules"] == []
    assert plan["planHash"].startswith("sha256:")


def test_locator_rejects_unregistered_module():
    bad = deepcopy(request())
    bad["targets"][0]["registryModules"] = ["imaginary_runtime"]
    with pytest.raises(UpdateLocatorError, match="REGISTRY_MODULE_UNKNOWN"):
        compile_plan(registry=registry(), policy=policy(), request=bad)


def test_locator_rejects_evidence_path_outside_policy():
    bad = deepcopy(request())
    bad["targets"][0]["evidencePaths"] = ["src/services/not_authorized.py"]
    with pytest.raises(UpdateLocatorError, match="EVIDENCE_PATH_NOT_ALLOWED"):
        compile_plan(registry=registry(), policy=policy(), request=bad)


def test_locator_rejects_policy_forbidden_module():
    bad = deepcopy(request())
    bad["targets"][0]["registryModules"] = ["frontend_view"]
    with pytest.raises(UpdateLocatorError, match="REGISTRY_MODULE_NOT_ALLOWED"):
        compile_plan(registry=registry(), policy=policy(), request=bad)
