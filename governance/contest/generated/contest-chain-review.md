# Contest Chain Review

- Review state: `REVIEW_PENDING`
- Baseline commit: `b5a14ab5898ba928df8a5074dcc6b70d299badae`
- Graph hash: `sha256:95cc643fcf7b468bf86e1dbbfd65fe29fc40392849aa2b752a07413226902d00`
- Selection hash: `sha256:d6d19db916506c22542bbf68e01871330eae0efd110d9abd9a9d05d82e071c7e`
- Registry adapter state: `REGISTRY_DOCUMENTS_VERIFIED_ROOT_EQUIVALENCE_PENDING`

## Summary

- KEEP_CORE: 14
- KEEP_SUPPORT: 2
- ISOLATE: 7
- REMOVE_CANDIDATE: 0
- REVIEW_REQUIRED: 0

## Machine findings

- `LAYERED_REGISTRY_ROOTS_NOT_EQUIVALENT`
- `REGISTERED_RUNNER_DRIFT_REVIEW_REQUIRED`
- `TOMBSTONE_REFERENCES_REMAIN`
- `Z_INTERFACE_PRODUCT_REGISTRY_PROTOCOL_REVIEW_REQUIRED`

## KEEP_CORE

| Module | Status | Owner | Upstream | Downstream | Physical paths |
|---|---|---|---|---|---|
| action_pack | ACTIVE | capability_platform | agent1_runtime | agent2_input_projection | src/services/agent_pipeline_item_worker_v2010_service.py |
| agent1_input_projection | ACTIVE | agent_platform | pipeline_runtime | agent1_runtime | src/services/agent_input_contract_v2258_service.py<br>src/services/agent_input_transport_v2258_service.py<br>src/services/hard_interface_bridge_v2301_service.py |
| agent1_runtime | ACTIVE | agent_platform | agent1_input_projection | action_pack | src/services/agent_input_contract_v2258_service.py<br>src/services/agent_runtime_hard_interface_v22514_service.py<br>src/services/agent_runtime_hard_interface_v22515_service.py<br>src/services/agent_runtime_hard_interface_v2257_service.py<br>src/services/agent_token_runtime_hash_exact_v2259_service.py<br>src/services/hash_directed_artifact_runtime_v2259_service.py<br>src/services/real_product_judgment_agent_v2259_service.py<br>src/services/station_agent_worker_v22515_service.py<br>src/services/station_agent_worker_v2259_service.py |
| agent2_input_projection | ACTIVE | agent_platform | action_pack | agent2_runtime | src/services/agent_input_transport_v230_service.py |
| agent2_runtime | ACTIVE | agent_platform | agent2_input_projection | agent3_input_projection | src/services/agent2_action_draft_core_v225_service.py<br>src/services/agent2_hash_proof_bridge_v22515_service.py<br>src/services/agent2_runtime_v22515_service.py<br>src/services/agent_runtime_contract_v225_service.py<br>src/services/agent_runtime_hard_interface_v22515_service.py<br>src/services/hash_directed_artifact_runtime_v2259_service.py |
| agent3_input_projection | ACTIVE | agent_platform | agent2_runtime | agent3_runtime | src/services/agent3_system_constraint_base_v23214_service.py<br>src/services/agent3_system_constraint_v23213_service.py<br>src/services/agent3_system_constraint_v23214_service.py<br>src/services/agent3_system_constraint_v23215_service.py<br>src/services/agent_input_contract_v225_service.py<br>src/services/agent_input_transport_v225_service.py |
| agent3_runtime | ACTIVE | agent_platform | agent3_input_projection | task_mapping | src/services/agent3_runtime_v23215_service.py<br>src/services/agent3_sop_core_v225_service.py<br>src/services/agent3_system_constraint_base_v23214_service.py<br>src/services/agent3_system_constraint_v23213_service.py<br>src/services/agent3_system_constraint_v23214_service.py<br>src/services/agent3_system_constraint_v23215_service.py<br>src/services/agent_runtime_contract_v225_service.py<br>src/services/agent_token_runtime_v2259_service.py<br>src/services/agent_token_runtime_v225_service.py<br>src/services/pipeline_agent3_sop_v225_service.py |
| artifact_execution_runtime | ACTIVE | transport_platform | artifact_transport | agent1_runtime<br>agent2_runtime | src/services/hash_directed_artifact_runtime_v2259_service.py |
| artifact_transport | ACTIVE | transport_platform | - | artifact_execution_runtime | src/services/artifact_transport_service.py |
| frontend_view | ACTIVE | frontend_platform | task_pool | - | config/v23_registry_runtime.json<br>src/api/routes/frontend_views.py<br>src/services/frontend_view_artifact_v2259_service.py<br>src/services/public_task_dto_service.py<br>web_demo/bootstrap.js<br>web_demo/core/router.js<br>web_demo/core/task-read-model-v2082.js<br>web_demo/index.html<br>web_demo/loading-ui.css<br>web_demo/modules/task-report/page.js |
| pipeline_runtime | ACTIVE | pipeline_platform | signal_admission | agent1_runtime | src/services/pipeline_item_service.py |
| signal_admission | ACTIVE | signal_platform | - | pipeline_runtime | src/services/artifact_signal_admission_v225_service.py |
| task_mapping | ACTIVE | task_platform | agent3_runtime | task_pool | src/services/agent_runtime_contract_v225_service.py<br>src/services/pipeline_artifact_contract_service.py<br>src/services/pipeline_task_mapping_v225_service.py |
| task_pool | ACTIVE | task_platform | task_mapping | frontend_view | src/services/lifecycle_task_v183_service.py<br>src/services/pipeline_artifact_contract_service.py<br>src/services/pipeline_task_mapping_v225_service.py<br>src/services/task_pool_admission_core_v20_service.py<br>src/services/task_snapshot_station_service.py |

## KEEP_SUPPORT

| Module | Status | Owner | Upstream | Downstream | Physical paths |
|---|---|---|---|---|---|
| registry_compiler | AUDIT_ONLY | platform_governance | - | release_governance | tools/registry_compiler/compile_registry.py |
| release_governance | ACTIVE | platform_governance | registry_compiler | - | config/v23_registry_runtime.json<br>release/release-policy.json<br>scripts/deploy_release.sh<br>scripts/start_server.sh<br>src/deployment/deploy_release_core_v22516.sh<br>src/services/registry_runtime_receipt_v23_service.py<br>src/services/release_identity_service.py |

## ISOLATE

| Module | Status | Owner | Upstream | Downstream | Physical paths |
|---|---|---|---|---|---|
| action_node_transport | REGISTERED_ONLY | transport_platform | - | - | tools/registry_compiler/v24_identity_catalog.py |
| execution_resource_orchestrator | REGISTERED_ONLY | capability_platform | - | - | tools/registry_compiler/v24_identity_catalog.py |
| node_authorization | REGISTERED_ONLY | authorization_platform | - | - | tools/registry_compiler/v24_identity_catalog.py |
| operating_plan_compiler | REGISTERED_ONLY | plan_platform | - | - | tools/registry_compiler/v24_identity_catalog.py |
| stage_frontend_projection | REGISTERED_ONLY | frontend_platform | - | - | tools/registry_compiler/v24_identity_catalog.py |
| stage_lifecycle | REGISTERED_ONLY | task_platform | - | - | tools/registry_compiler/v24_identity_catalog.py |
| task_blueprint_compiler | REGISTERED_ONLY | task_platform | - | - | tools/registry_compiler/v24_task_blueprint_compiler.py |

## REMOVE_CANDIDATE

| Module | Status | Owner | Upstream | Downstream | Physical paths |
|---|---|---|---|---|---|

## REVIEW_REQUIRED

| Module | Status | Owner | Upstream | Downstream | Physical paths |
|---|---|---|---|---|---|

## Approval gate

No physical deletion is authorized. Move to `APPROVED` only after human review of
root equivalence, active unresolved modules, tombstone hits, and shared physical paths.
