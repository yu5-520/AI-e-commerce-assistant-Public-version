"""Exact graph validation and fail-closed acceptance of a scoped revision.

A scope is supplied by the Java authority through the authorized input Artifact;
this module does not grant scopes and never trusts model-authored revision fields.
"""
from __future__ import annotations
from src.services.v26_sop_evidence_service import digest


def verify_graph(graph):
    if not isinstance(graph, dict) or graph.get("graphHash") != digest({k: v for k, v in graph.items() if k != "graphHash"}):
        raise ValueError("revision_graph_content_mismatch")
    nodes = graph.get("nodes", [])
    by_key = {}
    for node in nodes:
        expected = digest({k: v for k, v in node.items() if k != "nodeHash"})
        if node.get("nodeHash") != expected or node.get("nodeKey") in by_key:
            raise ValueError("revision_node_content_mismatch")
        by_key[node["nodeKey"]] = node
    for edge in graph.get("edges", []):
        if edge.get("edgeHash") != digest({k: v for k, v in edge.items() if k != "edgeHash"}):
            raise ValueError("revision_edge_content_mismatch")
        for side in ("source", "target"):
            node = by_key.get(edge.get(side + "Key"))
            if node is not None and node.get("nodeHash") != edge.get(side + "NodeHash"):
                raise ValueError("revision_edge_node_mismatch")
    return by_key


def verify_revision_result(parent, target, scope, *, graph_kind):
    """Validate preserved content and forbid silent scope expansion on acceptance."""
    if graph_kind not in {"Judgement", "Action", "Operation"}:
        raise ValueError("revision_graph_kind_invalid")
    before, after = verify_graph(parent), verify_graph(target)
    if scope.get("parent" + graph_kind + "GraphHash") != parent["graphHash"]:
        raise ValueError("revision_parent_mismatch")
    if scope.get("revisionHash") != digest({k: v for k, v in scope.items() if k != "revisionHash"}):
        raise ValueError("revision_scope_content_mismatch")
    reopened = set(scope.get("reopen" + graph_kind + "NodeHashes", []))
    preserved = set(scope.get("preserved" + graph_kind + "NodeHashes", []))
    hashes = {n["nodeHash"] for n in before.values()}
    if reopened & preserved or reopened | preserved != hashes:
        raise ValueError("revision_scope_coverage_invalid")
    # New/deleted node identities require a separately authorized structural revision.
    if before.keys() != after.keys():
        raise ValueError("revision_node_membership_change_not_authorized")
    for key, node in before.items():
        if node["nodeHash"] in preserved and node != after[key]:
            raise ValueError("revision_preserved_node_changed")
    return {"verified": True, "revisionHash": scope["revisionHash"], "targetGraphHash": target["graphHash"]}
