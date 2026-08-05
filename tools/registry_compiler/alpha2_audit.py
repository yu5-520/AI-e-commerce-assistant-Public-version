"""Combined V23.0.0-alpha.2 registry completeness audit CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .compile_registry import audit_registry, sha256_value
from .registry_graph import build_dependency_graph, calculate_impact
from .repository_audit import scan_repository

ALPHA2_VERSION = "23.0.0-alpha.2"


def run_alpha2_audit(
    root: Path | None = None,
    *,
    changed_fields: Iterable[str] = (),
    changed_schemas: Iterable[str] = (),
    changed_modules: Iterable[str] = (),
    changed_interfaces: Iterable[str] = (),
    changed_stations: Iterable[str] = (),
) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    registry = audit_registry(repository)
    graph = build_dependency_graph(repository)
    repository_scan = scan_repository(repository)
    impact = calculate_impact(
        repository,
        changed_fields=changed_fields,
        changed_schemas=changed_schemas,
        changed_modules=changed_modules,
        changed_interfaces=changed_interfaces,
        changed_stations=changed_stations,
    )
    material = {
        "registryRootHash": registry.get("registryRootHash"),
        "graphHash": graph.get("graphHash"),
        "repositoryScanHash": repository_scan.get("repositoryScanHash"),
        "impactHash": impact.get("impactHash"),
    }
    return {
        "schema": "registry.alpha2_completeness_audit.v1",
        "version": ALPHA2_VERSION,
        "mode": "report_only",
        "verifiedRegistry": registry.get("verified") is True,
        "auditRootHash": sha256_value(material),
        "hashLineage": material,
        "registry": registry,
        "dependencyGraph": graph,
        "repositoryScan": repository_scan,
        "impact": impact,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "deploymentBlocked": False,
    }


def _extend(parser: argparse.ArgumentParser, name: str, help_text: str) -> None:
    parser.add_argument(name, action="append", default=[], help=help_text)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the report-only V23 alpha.2 registry impact and repository audit."
    )
    _extend(parser, "--changed-field", "Changed fieldId; may be supplied more than once.")
    _extend(parser, "--changed-schema", "Changed schemaId; may be supplied more than once.")
    _extend(parser, "--changed-module", "Changed moduleId; may be supplied more than once.")
    _extend(parser, "--changed-interface", "Changed interfaceId; may be supplied more than once.")
    _extend(parser, "--changed-station", "Changed stationId; may be supplied more than once.")
    parser.add_argument("--output", help="Optional JSON report path under the repository.")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    report = run_alpha2_audit(
        root,
        changed_fields=args.changed_field,
        changed_schemas=args.changed_schema,
        changed_modules=args.changed_module,
        changed_interfaces=args.changed_interface,
        changed_stations=args.changed_station,
    )
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        target = (root / args.output).resolve()
        if root not in target.parents and target != root:
            raise SystemExit("output_path_must_be_inside_repository")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("verifiedRegistry") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
