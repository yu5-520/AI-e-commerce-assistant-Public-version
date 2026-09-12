"""V26.7 SOP evidence: freeze at execution, verify/project without provider calls.

This is a read model, not an authority or a second task runtime. Missing historical
receipts remain missing. Never reconstruct a model's private reasoning on a GET.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any

VERSION = "26.7.0"
SCHEMA = "v26.sop_evidence.v1"


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def obj(value):
    return value if isinstance(value, dict) else {}


def seal(body):
    result = deepcopy(body)
    result["receiptHash"] = digest(body)
    return result


def verified(receipt):
    if not isinstance(receipt, dict) or not receipt.get("receiptHash"):
        return False
    try:
        return receipt["receiptHash"] == digest({k: v for k, v in receipt.items() if k != "receiptHash"})
    except (ValueError, TypeError):
        return False


def freeze_decision_evidence(package, sop):
    """Only accepted structured outputs, never raw prompt/provider response."""
    draft = obj(package.get("agent2ActionDraft"))
    graphs = [obj(package.get("v26JudgementGraph")), obj(draft.get("v26ActionGraph")), obj(sop.get("v26OperationGraph"))]
    cards = []
    allowed = {
        "judgement.reasoning": "判断依据", "judgement.primary_issue": "任务问题",
        "judgement.evidence_refs": "引用证据", "plan.strategy_summary": "方案依据",
        "plan.candidate_strategies": "已记录候选方案", "plan.selected_strategy": "选择方案",
        "plan.daily_budget": "每日预算", "plan.target_roas": "目标 ROAS",
        "plan.review_window": "观察窗口", "plan.lower_guard": "下限",
        "plan.upper_guard": "上限", "plan.minimum_evidence": "最低证据要求",
        "plan.judgement_refs": "判断引用", "operation_stage.action_refs": "动作引用",
        "operation_stage.objective": "步骤目标", "operation_stage.rollback": "停止与恢复条件",
    }
    for graph in graphs:
        if not graph: continue
        material = {k: v for k, v in graph.items() if k != "graphHash"}
        if graph.get("graphHash") != digest(material):
            raise ValueError("sop_evidence_graph_hash_mismatch")
        for node in graph.get("nodes", []):
            headers = obj(node.get("authorityHeaders"))
            for header, label in allowed.items():
                if header not in headers: continue
                value = headers[header]
                # Only bounded JSON from the already-validated structured output.
                if len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 12000:
                    raise ValueError("sop_evidence_card_budget")
                cards.append({"label": label, "field": header, "value": deepcopy(value),
                    "kind": "PLAN" if header.startswith("plan.") else "DECISION",
                    "nodeKey": node.get("nodeKey"), "nodeHash": node.get("nodeHash"),
                    "sourceHash": graph["graphHash"], "actor": graph.get("actor"),
                    "status": "RECORDED", "formula": None})
    knowledge = obj(package.get("unifiedKnowledge"))
    # Receipts are summaries; no RAG documents/full database content is published.
    knowledge_summary = {k: deepcopy(knowledge[k]) for k in
        ("version", "envelopeHash", "compositionHash", "planHash", "indexManifestHash", "retrievalReceiptHash", "selectedRevisionIds") if k in knowledge}
    knowledge_summary["compositionHash"] = obj(knowledge.get("composition")).get("compositionHash")
    knowledge_summary["formalItemCount"] = len(knowledge.get("formalKnowledgeItems", []))
    knowledge_summary["gapCount"] = len(knowledge.get("insufficientEvidence", []))
    knowledge_summary["selectedRevisions"] = [{k: item[k] for k in ("revisionId", "fieldHash", "canonicalField", "contentHash") if k in item}
        for item in knowledge.get("formalKnowledgeItems", []) if isinstance(item, dict)]
    return seal({"schema": SCHEMA, "version": VERSION, "source": "accepted_agent_execution",
        "executionIdentity": {k: sop[k] for k in ("itemExecutionId", "inputContentHash", "productId", "storeId") if k in sop},
        "cards": cards, "knowledge": knowledge_summary,
        "knowledgeEffect": "NOT_EVALUATED", "feedbackStatus": "NOT_RECORDED",
        "modelReasoning": "structured_decision_record_only", "onClickProviderCall": False})


def freeze_metric_evidence(projection):
    """Recompute differences from the SAME frozen metric/window; never invent ROAS formula."""
    observations = projection.get("recentSnapshots") or []
    labels = {d.get("code"): d.get("label", d.get("code")) for d in projection.get("metricDefinitions", [])}
    cards = []
    for index, observation in enumerate(observations):
        for code, value in obj(observation.get("metrics")).items():
            if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value): continue
            source = {"snapshotId": observation.get("snapshotId"), "dataVersion": observation.get("dataVersion"),
                "businessDate": observation.get("businessDate"), "field": code, "value": value}
            cards.append({"label": labels.get(code, code), "kind": "FACT", "value": value,
                "status": "SOURCE_RECORDED", "formula": None, "inputs": [source],
                "reason": "报表观测值；原始计算过程未记录时不补造公式。"})
            if index == 0: continue
            previous = observations[index - 1]
            baseline = obj(previous.get("metrics")).get(code)
            if not isinstance(baseline, (int, float)) or isinstance(baseline, bool) or not math.isfinite(baseline): continue
            delta = value - baseline
            if not math.isfinite(delta): continue
            cards.append({"label": labels.get(code, code) + "变化量", "kind": "DERIVED",
                "value": delta, "status": "RECOMPUTED", "formula": "current - previous",
                "formulaVersion": "difference.v1", "inputs": [
                    {"snapshotId": previous.get("snapshotId"), "dataVersion": previous.get("dataVersion"),
                     "businessDate": previous.get("businessDate"), "field": code, "value": baseline}, source]})
    return seal({"schema": "v26.metric_evidence.v1", "version": VERSION, "cards": cards,
        "sourceDataVersion": projection.get("sourceDataVersion"), "frozenAt": projection.get("frozenAt"),
        "historyIdentityHash": projection.get("historyIdentityHash")})


def public_evidence(decisions, metrics):
    """Do not pass through unknown fields, raw prompts, or storage metadata."""
    receipts = [("decisions", decisions), ("metrics", metrics)]
    result = {"version": VERSION, "cards": [], "receipts": [], "missing": []}
    for name, receipt in receipts:
        if not verified(receipt):
            result["missing"].append(name + (":invalid" if receipt else ":not_recorded"))
            continue
        for card in receipt.get("cards", []):
            result["cards"].append({k: deepcopy(card[k]) for k in
                ("label", "kind", "value", "status", "formula", "formulaVersion", "inputs", "reason", "actor", "nodeKey", "nodeHash", "sourceHash") if k in card})
        result["receipts"].append({"kind": name, "hash": receipt["receiptHash"], "status": "VERIFIED"})
    if verified(decisions):
        result["knowledge"] = deepcopy(decisions.get("knowledge", {}))
    result.update(knowledgeEffect="尚无对照评测证据", feedbackStatus="未记录审核回流结果",
        privateReasoningExposed=False, recomputedOnRead=False)
    return result


def read_task_knowledge_audit(task_id):
    """Read only task-linked V25 revision/review records; never scan arbitrary knowledge."""
    from src.repositories.sqlite_repository import connect
    result = {"status": "NOT_RECORDED", "revisions": [], "events": [], "reuseEvents": []}
    with connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('rag_knowledge_revisions','rag_knowledge_review_events','rag_knowledge_reuse_events','rag_retrieval_observations','rag_knowledge_index_manifests','rag_knowledge_index_head')")}
        if 'rag_knowledge_revisions' not in tables: return result
        rows = conn.execute("SELECT revision_id,content_hash,content_json,source_recap_hash,previous_revision_id,created_at FROM rag_knowledge_revisions WHERE source_task_id=? ORDER BY created_at DESC,revision_id DESC LIMIT 50", (str(task_id),)).fetchall()
        valid_ids = set()
        for row in rows:
            try:
                content_hash = digest(json.loads(row['content_json'])).removeprefix('sha256:')
            except (ValueError, TypeError):
                result['status'] = 'INVALID_EVIDENCE'; continue
            if content_hash != str(row['content_hash']).removeprefix('sha256:'):
                result['status'] = 'INVALID_EVIDENCE'; continue
            valid_ids.add(row['revision_id'])
            result['revisions'].append({'revisionId':row['revision_id'], 'contentHash':row['content_hash'],
                'sourceRecapHash':row['source_recap_hash'], 'previousRevisionId':row['previous_revision_id'],
                'createdAt':row['created_at']})
        if valid_ids and 'rag_knowledge_review_events' in tables:
            # One task-scoped join; bounded output, no N+1 queries.
            events = conn.execute("SELECT e.* FROM rag_knowledge_review_events e JOIN rag_knowledge_revisions r ON r.revision_id=e.revision_id WHERE r.source_task_id=? ORDER BY e.created_at DESC,e.event_hash DESC LIMIT 100", (str(task_id),)).fetchall()
            for row in events:
                if row['revision_id'] not in valid_ids: continue
                material={'revisionId':row['revision_id'],'decision':row['decision'],'reviewerId':row['reviewer_id'],
                    'reason':row['reason'],'beforeHash':row['before_hash'],'afterHash':row['after_hash'],'migration':bool(row['migration'])}
                if digest(material).removeprefix('sha256:') != str(row['event_hash']).removeprefix('sha256:'):
                    result['status']='INVALID_EVIDENCE'; continue
                result['events'].append({k:material[k] for k in ('revisionId','decision','reason','beforeHash','afterHash')}
                    | {'eventHash':row['event_hash'],'createdAt':row['created_at']})
        if valid_ids and 'rag_knowledge_reuse_events' in tables:
            rows = conn.execute("SELECT e.* FROM rag_knowledge_reuse_events e JOIN rag_knowledge_revisions r ON r.revision_id=e.revision_id WHERE r.source_task_id=? ORDER BY e.created_at DESC,e.event_hash DESC LIMIT 101", (str(task_id),)).fetchall()
            truncated = len(rows) > 100
            invalid = 0
            for row in rows[:100]:
                if row['revision_id'] not in valid_ids:
                    invalid += 1
                    continue
                material = {'revisionId': row['revision_id'], 'retrievalReceiptHash': row['retrieval_receipt_hash'],
                            'outcome': row['outcome'], 'actorId': row['actor_id'], 'notes': row['notes']}
                if (row['outcome'] not in {'success', 'failure', 'neutral'} or
                    digest(material).removeprefix('sha256:') != str(row['event_hash']).removeprefix('sha256:')):
                    invalid += 1
                    result['status'] = 'INVALID_EVIDENCE'
                    continue
                result['reuseEvents'].append({k: material[k] for k in ('revisionId', 'retrievalReceiptHash', 'outcome')}
                    | {'eventHash': row['event_hash'], 'createdAt': row['created_at'], 'integrity': 'VERIFIED',
                       'retrievalReceiptVerification': 'NOT_CHECKED'})
            if 'rag_retrieval_observations' in tables:
                bind_retrieval_proofs(conn, result['reuseEvents'])
            if 'rag_knowledge_index_manifests' in tables:
                bind_index_proofs(conn, result['reuseEvents'], result['revisions'], tables)
            result['reuseSummary'] = summarize_reuse_evidence(result['reuseEvents'], truncated=truncated, invalid=invalid)
    if result['status'] != 'INVALID_EVIDENCE' and result['revisions']: result['status']='RECORDED'
    return result


def summarize_reuse_evidence(events, *, truncated=False, invalid=0):
    """Descriptive statistics of the displayed verified records, not a causal eval."""
    total = len(events)
    successes = sum(event['outcome'] == 'success' for event in events)
    failures = sum(event['outcome'] == 'failure' for event in events)
    return {'status': 'RECORDED' if total else 'NOT_RECORDED', 'total': total,
            'success': successes, 'failure': failures, 'neutral': total - successes - failures,
            'verifiedRetrievalCount': sum(e.get('retrievalReceiptVerification') == 'VERIFIED' for e in events),
            'successRate': successes / total if total else None,
            'formula': 'success / (success + failure + neutral)',
            'formulaVersion': 'recorded_reuse_success_rate.v1',
            'inputs': {'success': successes, 'failure': failures, 'neutral': total - successes - failures},
            'eventHashes': [event['eventHash'] for event in events],
            'scope': 'displayed_verified_events_for_task_origin_revisions',
            'maxRecords': 100, 'truncated': truncated, 'invalidRecordCount': invalid,
            'causalEffectEvaluated': False, 'automaticLifecycleChange': False}


def verify_retrieval_observation(row, revision_id):
    """Verify V25.13 observation and original V25.12 receipt independently."""
    try:
        matched = json.loads(row['matched_revision_ids_json'])
        if not isinstance(matched, list) or not all(isinstance(x, str) for x in matched):
            raise ValueError('invalid matched revisions')
        receipt = {'schema': 'rag.knowledge_retrieval_receipt.v1',
                   'queryFingerprint': row['query_fingerprint'], 'knowledgeSnapshotHash': row['knowledge_snapshot_hash'],
                   'indexVersion': row['index_version'], 'indexManifestHash': row['index_manifest_hash'],
                   'retrievalPolicyVersion': row['retrieval_policy_version'], 'matchedRevisionIds': matched}
        material = {k: v for k, v in receipt.items() if k != 'schema'}
        material.update(schema='rag.retrieval_observation.v1', version='25.13.0',
                        candidateCount=row['candidate_count'], eligibleCount=row['eligible_count'],
                        matchedCount=row['matched_count'], filteredLifecycleCount=row['filtered_lifecycle_count'],
                        latencyMs=row['latency_ms'], retrievalReceiptHash=row['retrieval_receipt_hash'])
        for body, declared in ((receipt, row['retrieval_receipt_hash']), (material, row['observation_hash'])):
            if digest(body).removeprefix('sha256:') != str(declared).removeprefix('sha256:'):
                raise ValueError('hash mismatch')
        counts = [row[k] for k in ('candidate_count','eligible_count','matched_count','filtered_lifecycle_count')]
        if any(type(x) is not int or x < 0 for x in counts):
            raise ValueError('invalid counts')
        candidate, eligible, count, filtered = counts
        if not (count <= eligible <= candidate and filtered == candidate - eligible and count == len(set(matched)) == len(matched)):
            raise ValueError('inconsistent counts')
        if not isinstance(row['latency_ms'], (int, float)) or not math.isfinite(row['latency_ms']) or row['latency_ms'] < 0:
            raise ValueError('invalid latency')
    except (ValueError, TypeError, KeyError):
        return {'retrievalReceiptVerification': 'INVALID_EVIDENCE'}
    if revision_id not in matched:
        return {'retrievalReceiptVerification': 'REVISION_NOT_MATCHED'}
    return {'retrievalReceiptVerification': 'VERIFIED', 'retrievalProof': {
        'observationHash': row['observation_hash'], 'indexManifestHash': row['index_manifest_hash'],
        'indexVersion': row['index_version'],
        'knowledgeSnapshotHash': row['knowledge_snapshot_hash'], 'retrievalPolicyVersion': row['retrieval_policy_version'],
        'candidateCount': candidate, 'eligibleCount': eligible, 'matchedCount': count,
        'filteredLifecycleCount': filtered, 'latencyMs': row['latency_ms'],
        'selectedShare': count / eligible if eligible else None,
        'formula': 'matchedCount / eligibleCount', 'formulaVersion': 'retrieval_selected_share.v1',
        'indexManifestVerification': 'NOT_CHECKED', 'subsequentTaskBinding': 'NOT_CHECKED'}}


def bind_retrieval_proofs(conn, events):
    receipts = sorted({event['retrievalReceiptHash'] for event in events})
    if not receipts:
        return
    # One bounded request set; each lookup uses the existing receipt index.
    requested = ','.join('(?)' for _ in receipts)
    rows = conn.execute(f"WITH requested(receipt) AS (VALUES {requested}) SELECT o.* FROM requested r JOIN rag_retrieval_observations o ON o.observation_hash=(SELECT observation_hash FROM rag_retrieval_observations WHERE retrieval_receipt_hash=r.receipt ORDER BY recorded_at DESC,observation_hash DESC LIMIT 1)", receipts).fetchall()
    observations = {row['retrieval_receipt_hash']: row for row in rows}
    for event in events:
        row = observations.get(event['retrievalReceiptHash'])
        event.update(verify_retrieval_observation(row, event['revisionId']) if row is not None
                     else {'retrievalReceiptVerification': 'NOT_RECORDED'})


def verify_index_manifest(row, proof, revision):
    """Recompute the exact V25.12 identity, not unsealed manifest display metadata."""
    try:
        manifest = json.loads(row['manifest_json'])
        keys = ('knowledgeIndexId', 'indexVersion', 'knowledgeSnapshotHash', 'sourceRevisionSetHash',
                'retrievalContractVersion', 'indexEngine', 'activeRevisions')
        identity = {k: manifest[k] for k in keys}
        active = identity['activeRevisions']
        if not isinstance(active, list) or not all(isinstance(x, dict) for x in active):
            raise ValueError('invalid active revisions')
        ids = [x['revisionId'] for x in active]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate revision')
        def same_hash(body, declared):
            return digest(body).removeprefix('sha256:') == str(declared).removeprefix('sha256:')
        if not (same_hash(identity, row['manifest_hash']) and
                str(manifest['manifestHash']) == str(row['manifest_hash']) == str(proof['indexManifestHash']) and
                same_hash(active, identity['knowledgeSnapshotHash']) and same_hash(ids, identity['sourceRevisionSetHash'])):
            raise ValueError('manifest hash mismatch')
        if (identity['indexVersion'] != proof['indexVersion'] or
            identity['knowledgeSnapshotHash'] != proof['knowledgeSnapshotHash'] or
            identity['retrievalContractVersion'] != proof['retrievalPolicyVersion']):
            raise ValueError('receipt manifest mismatch')
        matched = [x for x in active if x['revisionId'] == revision['revisionId']]
        if len(matched) != 1 or matched[0]['contentHash'] != revision['contentHash']:
            return {'indexManifestVerification': 'REVISION_CONTENT_MISMATCH'}
    except (ValueError, TypeError, KeyError, IndexError):
        return {'indexManifestVerification': 'INVALID_EVIDENCE'}
    return {'indexManifestVerification': 'VERIFIED', 'indexProof': {
        'manifestHash': row['manifest_hash'], 'indexVersion': identity['indexVersion'],
        'knowledgeSnapshotHash': identity['knowledgeSnapshotHash'],
        'sourceRevisionSetHash': identity['sourceRevisionSetHash'],
        'revisionId': revision['revisionId'], 'revisionContentHash': revision['contentHash'],
        'activeRevisionCount': len(active), 'productionActivationVerified': False}}


def bind_index_proofs(conn, events, revisions, tables):
    eligible = [e for e in events if e.get('retrievalReceiptVerification') == 'VERIFIED']
    hashes = sorted({e['retrievalProof']['indexManifestHash'] for e in eligible})
    if not hashes:
        return
    marks = ','.join('?' for _ in hashes)
    rows = conn.execute(f'SELECT manifest_hash,manifest_json FROM rag_knowledge_index_manifests WHERE manifest_hash IN ({marks})', hashes).fetchall()
    manifests = {row['manifest_hash']: row for row in rows}
    by_id = {r['revisionId']: r for r in revisions}
    head = None
    if 'rag_knowledge_index_head' in tables:
        row = conn.execute("SELECT current_manifest_hash FROM rag_knowledge_index_head WHERE head_key='knowledge'").fetchone()
        head = row['current_manifest_hash'] if row else None
    for event in eligible:
        proof = event['retrievalProof']
        row = manifests.get(proof['indexManifestHash'])
        proof.update(verify_index_manifest(row, proof, by_id[event['revisionId']]) if row is not None
                     else {'indexManifestVerification': 'NOT_RECORDED'})
        if proof['indexManifestVerification'] == 'VERIFIED':
            proof['indexProof']['headRelation'] = ('CURRENT_DATABASE_HEAD' if head == proof['indexManifestHash']
                                                    else 'HISTORICAL_MANIFEST' if head else 'HEAD_NOT_RECORDED')
