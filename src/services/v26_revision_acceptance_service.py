"""Exact graph validation and fail-closed acceptance of a scoped revision.

A scope is supplied by the Java authority through the authorized input Artifact;
this module does not grant scopes and never trusts model-authored revision fields.
"""
from __future__ import annotations
from src.services.v26_sop_evidence_service import digest


def verify_graph(graph):
    if isinstance(graph, dict) and graph.get('contractVersion') == '26.9.0':
        from src.services.v269_semantic_graph_service import index
        return index(graph)
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
    if graph_kind not in {"Judgement", "Action", "Operation", "Decision", "Plan"}:
        raise ValueError("revision_graph_kind_invalid")
    before, after = verify_graph(parent), verify_graph(target)
    if parent.get('contractVersion') == '26.9.0' or target.get('contractVersion') == '26.9.0':
        if parent.get('kind') != graph_kind + 'Graph' or target.get('kind') != parent.get('kind'):
            raise ValueError('revision_graph_kind_mismatch')
        if parent.get('contractHash') != target.get('contractHash'):
            raise ValueError('revision_contract_mismatch')
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
    # A preserved node must retain its incoming/outgoing relationships as well.
    preserved_keys = {k for k, n in before.items() if n['nodeHash'] in preserved}
    def protected_edges(graph):
        edges = []
        for edge in graph.get('edges', []):
            if edge.get('sourceRef', edge.get('sourceKey')) not in preserved_keys and edge.get('targetRef', edge.get('targetKey')) not in preserved_keys:
                continue
            material = {k:v for k,v in edge.items() if k != 'edgeHash'}
            for side in ('source', 'target'):
                if edge.get(side + 'Key') in before and edge[side + 'Key'] not in preserved_keys:
                    material.pop(side + 'NodeHash', None)
            edges.append(digest(material))
        return sorted(edges)
    if protected_edges(parent) != protected_edges(target):
        raise ValueError('revision_preserved_edge_changed')
    return {"verified": True, "revisionHash": scope["revisionHash"], "targetGraphHash": target["graphHash"]}


def freeze_revision_acceptance(parent, target, scope, *, graph_kind):
    from src.services.v26_sop_evidence_service import seal
    verify_revision_result(parent, target, scope, graph_kind=graph_kind)
    before, after = verify_graph(parent), verify_graph(target)
    rows = []
    for key in sorted(before):
        old, new = before[key], after[key]
        fields = []
        a, b = (
            ({k:v for k,v in old.items() if k!='nodeHash'}, {k:v for k,v in new.items() if k!='nodeHash'})
            if parent.get('contractVersion') == '26.9.0' else (old.get('authorityHeaders', {}), new.get('authorityHeaders', {})))
        for field in sorted(set(a) | set(b)):
            if field not in a or field not in b or a[field] != b[field]:
                fields.append({'field': field, 'beforePresent': field in a, 'afterPresent': field in b,
                               'before': a.get(field), 'after': b.get(field)})
        rows.append({'nodeKey': key, 'beforeHash': old['nodeHash'], 'afterHash': new['nodeHash'],
                     'changed': old != new, 'fields': fields})
    return seal({'schema': 'v26.revision_acceptance.v1', 'graphKind': graph_kind,
        'scopeHash': scope['revisionHash'], 'reviewHash': scope.get('reviewHash'),
        'parentGraphHash': parent['graphHash'], 'targetGraphHash': target['graphHash'],
        'nodes': rows, 'changedCount': sum(r['changed'] for r in rows),
        'unchangedCount': sum(not r['changed'] for r in rows),
        'formula': 'changedCount = count(beforeHash != afterHash)',
        'authorityOriginVerified': False, 'productionActivationVerified': False,
        'verification': 'CONTENT_AND_SCOPE_CONSISTENCY'})
