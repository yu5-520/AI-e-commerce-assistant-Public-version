# REGISTERED_ONLY Isolation Transaction

- State: `ISOLATION_VERIFIED_SIMULATION_READY`
- Transaction hash: `sha256:24048d63aa7726304285343f5e090c7d302c988fecaa9ae527dc213d9b685bcc`
- Selection hash: `sha256:c24f7797a66849f61226fd0debf7d24e1b4295df28004a6f705ec338dbab8beb`
- Module count: 7
- Physical path count: 2
- Simulation exclude candidates: 2
- Preserved shared paths: 0

## Module gates

| Module | Registry state | Runtime binding | Graph edges | Interfaces | Stations | Decision |
|---|---|---|---:|---:|---:|---|
| action_node_transport | REGISTERED_ONLY | disabled | 0 | 0 | 0 | VERIFIED_LOGICAL_ISOLATION |
| execution_resource_orchestrator | REGISTERED_ONLY | disabled | 0 | 0 | 0 | VERIFIED_LOGICAL_ISOLATION |
| node_authorization | REGISTERED_ONLY | disabled | 0 | 0 | 0 | VERIFIED_LOGICAL_ISOLATION |
| operating_plan_compiler | REGISTERED_ONLY | disabled | 0 | 0 | 0 | VERIFIED_LOGICAL_ISOLATION |
| stage_frontend_projection | REGISTERED_ONLY | disabled | 0 | 0 | 0 | VERIFIED_LOGICAL_ISOLATION |
| stage_lifecycle | REGISTERED_ONLY | disabled | 0 | 0 | 0 | VERIFIED_LOGICAL_ISOLATION |
| task_blueprint_compiler | REGISTERED_ONLY | disabled | 0 | 0 | 0 | VERIFIED_LOGICAL_ISOLATION |

## Physical paths

| Path | Claiming modules | Keep closure | Decision |
|---|---|---|---|
| tools/registry_compiler/v24_identity_catalog.py | action_node_transport<br>execution_resource_orchestrator<br>node_authorization<br>operating_plan_compiler<br>stage_frontend_projection<br>stage_lifecycle | no | SIMULATION_EXCLUDE_CANDIDATE |
| tools/registry_compiler/v24_task_blueprint_compiler.py | task_blueprint_compiler | no | SIMULATION_EXCLUDE_CANDIDATE |

## Safety boundary

This transaction verifies existing logical isolation only. It does not edit
the product Registry, enable or disable runtime bindings, move source files,
delete code, mutate databases, call Providers, deploy ECS, or modify `main`.
