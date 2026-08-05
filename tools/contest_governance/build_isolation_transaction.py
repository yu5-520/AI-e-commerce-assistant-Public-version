"""Build a report-only isolation transaction for REGISTERED_ONLY modules."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from tools.contest_governance.scope_unregistered_fields import (
    _closure,
    _registered_seeds,
)


SCHEMA = "contest.module_isolation_transaction.v1"


def _read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _records(document: Mapping[str, Any], key: str, identity: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in document.get(key) or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get(identity) or "")
        if item_id:
            result[item_id] = dict(raw)
    return result


def _runner_path(runner: str) -> Optional[str]:
    module_name, separator, _symbol = str(runner or "").partition(":")
    if not separator or not module_name:
        return None
    return "/".join(module_name.split(".")) + ".py"


def _path_claims(selection: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in selection.get("physicalPathReview") or []:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "")
        if path:
            result[path] = dict(raw)
    return result


def _render_markdown(transaction: Mapping[str, Any]) -> str:
    summary = dict(transaction.get("summary") or {})
    lines = [
        "# REGISTERED_ONLY Isolation Transaction",
        "",
        f"- State: `{transaction.get('state')}`",
        f"- Transaction hash: `{transaction.get('transactionHash')}`",
        f"- Selection hash: `{transaction.get('selectionHash')}`",
        f"- Module count: {summary.get('moduleCount', 0)}",
        f"- Physical path count: {summary.get('physicalPathCount', 0)}",
        f"- Simulation exclude candidates: {summary.get('simulationExcludePathCount', 0)}",
        f"- Preserved shared paths: {summary.get('preserveSharedPathCount', 0)}",
        "",
        "## Module gates",
        "",
        "| Module | Registry state | Runtime binding | Graph edges | Interfaces | Stations | Decision |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for item in transaction.get("modules") or []:
        lines.append(
            "| {module} | {status} | {binding} | {edges} | {interfaces} | {stations} | {decision} |".format(
                module=item.get("moduleId"),
                status=item.get("registryStatus"),
                binding="disabled" if item.get("runtimeBindingDisabled") else "REVIEW",
                edges=item.get("graphEdgeCount", 0),
                interfaces=len(item.get("interfaceIds") or []),
                stations=len(item.get("stationIds") or []),
                decision=item.get("isolationDecision"),
            )
        )
    lines.extend(
        [
            "",
            "## Physical paths",
            "",
            "| Path | Claiming modules | Keep closure | Decision |",
            "|---|---|---|---|",
        ]
    )
    for item in transaction.get("physicalPaths") or []:
        lines.append(
            "| {path} | {modules} | {closure} | {decision} |".format(
                path=item.get("path"),
                modules="<br>".join(item.get("claimingModuleIds") or []),
                closure="yes" if item.get("inKeepClosure") else "no",
                decision=item.get("physicalDecision"),
            )
        )
    lines.extend(
        [
            "",
            "## Safety boundary",
            "",
            "This transaction verifies existing logical isolation only. It does not edit",
            "the product Registry, enable or disable runtime bindings, move source files,",
            "delete code, mutate databases, call Providers, deploy ECS, or modify `main`.",
            "",
        ]
    )
    return "\n".join(lines)


def build(root: Path, generated_dir: Path) -> Dict[str, Any]:
    selection = _read_object(generated_dir / "contest-selection-manifest.json")
    graph = _read_object(generated_dir / "hash-lineage-graph.json")
    review = _read_object(generated_dir / "contest-chain-review.json")
    field_scope = _read_object(generated_dir / "unregistered-field-scope-review.json")
    modules_doc = _read_object(root / "contracts" / "registry" / "modules.json")
    interfaces_doc = _read_object(root / "contracts" / "registry" / "interfaces.json")
    stations_doc = _read_object(root / "contracts" / "registry" / "stations.json")
    runtime_projection = _read_object(root / "config" / "v23_registry_runtime.json")

    modules = _records(modules_doc, "modules", "moduleId")
    interfaces = _records(interfaces_doc, "interfaces", "interfaceId")
    stations = _records(stations_doc, "stations", "stationId")
    module_review = {
        str(item.get("moduleId")): dict(item)
        for item in review.get("moduleReview") or []
        if isinstance(item, dict) and str(item.get("moduleId") or "")
    }
    isolate_ids = sorted(
        str(value)
        for value in (selection.get("classifications") or {}).get("ISOLATE") or []
    )
    expected_isolate_ids = {
        "action_node_transport",
        "execution_resource_orchestrator",
        "node_authorization",
        "operating_plan_compiler",
        "stage_frontend_projection",
        "stage_lifecycle",
        "task_blueprint_compiler",
    }
    if set(isolate_ids) != expected_isolate_ids:
        raise RuntimeError("ISOLATE_MODULE_SET_DRIFT")

    seeds = _registered_seeds(selection)
    core_closure, _ = _closure(root, seeds.get("KEEP_CORE") or set())
    support_closure, _ = _closure(root, seeds.get("KEEP_SUPPORT") or set())
    keep_closure = core_closure | support_closure
    claims = _path_claims(selection)
    runtime_modules = dict(runtime_projection.get("modules") or {})

    interface_ids_by_module: Dict[str, Set[str]] = defaultdict(set)
    for interface_id, owners in (graph.get("interfaces") or {}).items():
        for module_id in owners or []:
            interface_ids_by_module[str(module_id)].add(str(interface_id))
    station_ids_by_module: Dict[str, Set[str]] = defaultdict(set)
    for station_id, owners in (graph.get("stations") or {}).items():
        for module_id in owners or []:
            station_ids_by_module[str(module_id)].add(str(station_id))

    field_scope_isolate_count = int(
        (field_scope.get("summary", {}).get("classificationCounts") or {}).get(
            "ISOLATE_DEFER", 0
        )
        or 0
    )

    module_results: List[Dict[str, Any]] = []
    physical_modules: Dict[str, Set[str]] = defaultdict(set)
    findings: List[str] = []

    for module_id in isolate_ids:
        module = dict(modules.get(module_id) or {})
        review_item = dict(module_review.get(module_id) or {})
        runner = str(module.get("runner") or "")
        runner_path = _runner_path(runner)
        physical_paths = sorted(
            set(str(path) for path in review_item.get("physicalPaths") or [] if str(path))
            | ({runner_path} if runner_path else set())
        )
        for path in physical_paths:
            physical_modules[path].add(module_id)

        upstream = sorted(set(str(value) for value in module.get("upstream") or []))
        downstream = sorted(set(str(value) for value in module.get("downstream") or []))
        interface_ids = sorted(interface_ids_by_module.get(module_id) or [])
        station_ids = sorted(station_ids_by_module.get(module_id) or [])
        runtime_entry = dict(runtime_modules.get(module_id) or {})

        registry_only = (
            str(module.get("status") or "") == "REGISTERED_ONLY"
            or str(module.get("activationState") or "") == "REGISTERED_ONLY"
            or str(review_item.get("activationState") or "") == "REGISTERED_ONLY"
        )
        runtime_binding_disabled = not bool(
            runtime_entry.get("runtimeBindingEnabled", False)
        )
        no_graph_edges = not upstream and not downstream
        no_runtime_interfaces = not interface_ids
        no_runtime_stations = not station_ids
        gates_pass = all(
            [
                registry_only,
                runtime_binding_disabled,
                no_graph_edges,
                no_runtime_interfaces,
                no_runtime_stations,
            ]
        )
        if not gates_pass:
            findings.append(f"ISOLATION_GATE_FAILED:{module_id}")

        module_results.append(
            {
                "moduleId": module_id,
                "owner": module.get("owner"),
                "registryStatus": module.get("status"),
                "activationState": module.get("activationState")
                or review_item.get("activationState"),
                "runner": runner,
                "physicalPaths": physical_paths,
                "upstream": upstream,
                "downstream": downstream,
                "graphEdgeCount": len(upstream) + len(downstream),
                "interfaceIds": interface_ids,
                "stationIds": station_ids,
                "registeredInterfaceRecords": [
                    interfaces.get(interface_id) for interface_id in interface_ids
                ],
                "registeredStationRecords": [
                    stations.get(station_id) for station_id in station_ids
                ],
                "runtimeProjectionEntry": runtime_entry,
                "registryOnly": registry_only,
                "runtimeBindingDisabled": runtime_binding_disabled,
                "noGraphEdges": no_graph_edges,
                "noRuntimeInterfaces": no_runtime_interfaces,
                "noRuntimeStations": no_runtime_stations,
                "isolationDecision": (
                    "VERIFIED_LOGICAL_ISOLATION" if gates_pass else "REVIEW_REQUIRED"
                ),
            }
        )

    physical_results: List[Dict[str, Any]] = []
    simulation_exclude_paths: List[str] = []
    preserve_shared_paths: List[str] = []
    for path, claiming_isolate_modules in sorted(physical_modules.items()):
        claim = dict(claims.get(path) or {})
        all_claiming_modules = sorted(
            set(str(value) for value in claim.get("claimingModuleIds") or [])
            | set(claiming_isolate_modules)
        )
        claimant_classifications = sorted(
            set(str(value) for value in claim.get("claimantClassifications") or [])
        )
        shared_with_keep = any(
            value in {"KEEP_CORE", "KEEP_SUPPORT"}
            for value in claimant_classifications
        )
        in_keep_closure = path in keep_closure
        path_exists = (root / path).is_file()
        safe_simulation_exclude = (
            path_exists and not shared_with_keep and not in_keep_closure
        )
        physical_decision = (
            "SIMULATION_EXCLUDE_CANDIDATE"
            if safe_simulation_exclude
            else "PRESERVE_LOGICAL_ISOLATION_ONLY"
        )
        if safe_simulation_exclude:
            simulation_exclude_paths.append(path)
        else:
            preserve_shared_paths.append(path)
        physical_results.append(
            {
                "path": path,
                "pathExists": path_exists,
                "claimingModuleIds": all_claiming_modules,
                "claimantClassifications": claimant_classifications,
                "sharedWithKeep": shared_with_keep,
                "inCoreImportClosure": path in core_closure,
                "inSupportImportClosure": path in support_closure,
                "inKeepClosure": in_keep_closure,
                "physicalDecision": physical_decision,
                "physicalMutationAuthorized": False,
            }
        )

    all_module_gates_pass = all(
        item.get("isolationDecision") == "VERIFIED_LOGICAL_ISOLATION"
        for item in module_results
    )
    state = (
        "ISOLATION_VERIFIED_SIMULATION_READY"
        if all_module_gates_pass
        else "ISOLATION_REVIEW_REQUIRED"
    )
    material = {
        "schema": SCHEMA,
        "version": "1.0.0",
        "mode": "report_only",
        "state": state,
        "selectionHash": selection.get("selectionHash"),
        "graphHash": selection.get("graphHash"),
        "fieldScopeHash": field_scope.get("scopeHash"),
        "isolateModuleIds": isolate_ids,
        "modules": module_results,
        "physicalPaths": physical_results,
        "simulationExcludePaths": sorted(simulation_exclude_paths),
        "preserveSharedPaths": sorted(preserve_shared_paths),
        "fieldScopeIsolateCandidateCount": field_scope_isolate_count,
        "findings": sorted(set(findings)),
        "summary": {
            "moduleCount": len(module_results),
            "verifiedLogicalIsolationCount": sum(
                1
                for item in module_results
                if item.get("isolationDecision") == "VERIFIED_LOGICAL_ISOLATION"
            ),
            "physicalPathCount": len(physical_results),
            "simulationExcludePathCount": len(simulation_exclude_paths),
            "preserveSharedPathCount": len(preserve_shared_paths),
            "fieldScopeIsolateCandidateCount": field_scope_isolate_count,
        },
        "approval": {
            "logicalIsolation": "VERIFIED",
            "simulationExclusion": (
                "READY" if all_module_gates_pass else "BLOCKED"
            ),
            "physicalDeletion": "NOT_AUTHORIZED",
            "promotion": "NOT_AUTHORIZED",
        },
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalFilesMoved": 0,
        "physicalDeletionExecuted": False,
        "mainMutated": False,
    }
    return {**material, "transactionHash": _hash(material)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the REGISTERED_ONLY module isolation transaction."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated", default="governance/contest/generated")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).resolve()
    generated = (root / args.generated).resolve()
    transaction = build(root, generated)
    _write_json(generated / "module-isolation-transaction.json", transaction)
    (generated / "module-isolation-transaction.md").write_text(
        _render_markdown(transaction), encoding="utf-8"
    )
    receipt_material = {
        "schema": "contest.module_isolation_receipt.v1",
        "transactionHash": transaction["transactionHash"],
        "selectionHash": transaction.get("selectionHash"),
        "graphHash": transaction.get("graphHash"),
        "fieldScopeHash": transaction.get("fieldScopeHash"),
        "state": transaction.get("state"),
        "simulationExcludePaths": transaction.get("simulationExcludePaths"),
        "preserveSharedPaths": transaction.get("preserveSharedPaths"),
        "states": [
            "ISOLATE_MODULE_SET_LOCKED",
            "REGISTRY_ONLY_STATE_VERIFIED",
            "RUNTIME_BINDINGS_VERIFIED_DISABLED",
            "GRAPH_AND_ENDPOINT_GATES_VERIFIED",
            "PHYSICAL_PATH_POLICY_COMPILED",
            "SIMULATION_READY",
        ],
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalFilesMoved": 0,
        "physicalDeletionExecuted": False,
        "mainMutated": False,
    }
    receipt = {**receipt_material, "receiptHash": _hash(receipt_material)}
    _write_json(generated / "module-isolation-receipt.json", receipt)
    print(
        json.dumps(
            {
                "state": transaction.get("state"),
                "transactionHash": transaction.get("transactionHash"),
                "receiptHash": receipt.get("receiptHash"),
                "summary": transaction.get("summary"),
                "simulationExcludePaths": transaction.get(
                    "simulationExcludePaths"
                ),
                "preserveSharedPaths": transaction.get("preserveSharedPaths"),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0 if transaction.get("state") == "ISOLATION_VERIFIED_SIMULATION_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
