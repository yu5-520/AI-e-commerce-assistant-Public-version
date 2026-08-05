"""Build bounded artifact lineage graphs for replay and operations."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from src.repositories.artifact_repository import artifact_children, artifact_parents

ARTIFACT_LINEAGE_VERSION = "22.2.1"


def _public(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "artifactId": record.get("artifact_id") or record.get("artifactId"),
        "artifactType": record.get("artifact_type") or record.get("artifactType"),
        "schemaVersion": record.get("schema_version") or record.get("schemaVersion"),
        "contentHash": record.get("content_hash") or record.get("contentHash"),
        "dataVersion": record.get("data_version") or record.get("dataVersion"),
        "storeId": record.get("store_id") or record.get("storeId"),
        "productId": record.get("product_id") or record.get("productId"),
        "createdBy": record.get("created_by") or record.get("createdBy"),
        "status": record.get("status"),
        "relationType": record.get("relationType"),
        "createdAt": record.get("created_at") or record.get("createdAt"),
    }


def lineage_graph(
    artifact_id: str,
    *,
    max_depth: int = 5,
    max_nodes: int = 100,
) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    visited: Set[tuple[str, str]] = set()

    def walk(current: str, depth: int, direction: str) -> None:
        if depth >= max_depth or len(nodes) >= max_nodes:
            return
        marker = (current, direction)
        if marker in visited:
            return
        visited.add(marker)
        records = artifact_parents(current) if direction == "parents" else artifact_children(current)
        for record in records:
            item = _public(record)
            related = str(item.get("artifactId") or "")
            if not related:
                continue
            nodes[related] = item
            if direction == "parents":
                edges.append(
                    {
                        "from": related,
                        "to": current,
                        "relationType": item.get("relationType") or "derived_from",
                    }
                )
            else:
                edges.append(
                    {
                        "from": current,
                        "to": related,
                        "relationType": item.get("relationType") or "derived_from",
                    }
                )
            walk(related, depth + 1, direction)
            if len(nodes) >= max_nodes:
                return

    walk(artifact_id, 0, "parents")
    walk(artifact_id, 0, "children")
    return {
        "version": ARTIFACT_LINEAGE_VERSION,
        "rootArtifactId": artifact_id,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "nodes": list(nodes.values()),
        "edges": edges,
        "truncated": len(nodes) >= max_nodes,
    }


__all__ = ["ARTIFACT_LINEAGE_VERSION", "lineage_graph"]
