#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v25-phase5"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

"$PYTHON_BIN" scripts/verify_v25_phase5_rag_runtime.py \
  --output "$OUT_DIR/rag-runtime-verification.json"

find java-control-plane/src/main/java -name '*.java' -print0 \
  | sort -z \
  | xargs -0 javac -encoding UTF-8 -d "$CLASS_DIR"

java -cp "$CLASS_DIR" com.zcentury.v24.V25Phase5Main \
  --root "$ROOT_DIR" \
  --evidence "$OUT_DIR/rag-runtime-verification.json" \
  --policy governance/v25/phase5-rag-quant-eval-policy.json \
  --eval-contract governance/v25/rag-eval-contract-v25.json \
  --output "$OUT_DIR/phase5-verification-report.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

runtime = json.loads(Path('dist/v25-phase5/rag-runtime-verification.json').read_text(encoding='utf-8'))
report = json.loads(Path('dist/v25-phase5/phase5-verification-report.json').read_text(encoding='utf-8'))
assert runtime['verified'] is True
assert runtime['receiptBoundMetrics'] is True
assert runtime['manifestBoundMetrics'] is True
assert runtime['tamperedReceiptBlocked'] is True
assert runtime['metricSnapshotImmutable'] is True
assert runtime['groundTruthMetricsRequireEvalSet'] is True
assert runtime['evalSetImmutable'] is True
assert runtime['evalSetVersioned'] is True
assert runtime['evalRunImmutable'] is True
assert runtime['baseTargetEvalRequired'] is True
assert runtime['regressionGateBlocksDegradation'] is True
assert runtime['staleLeakGateBlocksDegradation'] is True
assert runtime['retrievalAnswerEvalSeparated'] is True
assert runtime['llmJudgeSoleReleaseAuthority'] is False
assert runtime['chineseKnowledgeCenterRegistered'] is True
assert runtime['directDatabaseMutationAllowed'] is False
assert runtime['activeRevisionInPlaceEditAllowed'] is False
assert runtime['phase4KnowledgeGovernanceRetained'] is True
assert runtime['physicalRagProviderReplaced'] is False
assert runtime['vectorIndexRequired'] is False
assert runtime['newAgentRuntimeIntroduced'] is False
assert runtime['knowledgeMayCreateSystemFact'] is False
assert report['verified'] is True
assert report['enforcementMode'] == 'VERSIONED_RAG_QUANT_EVAL_KNOWLEDGE_CENTER'
assert report['groundTruthMetricsRequireEvalSet'] is True
assert report['llmJudgeSoleReleaseAuthority'] is False
assert report['directDatabaseMutationAllowed'] is False
assert report['activeRevisionInPlaceEditAllowed'] is False
assert report['physicalRagProviderReplaced'] is False
assert report['vectorIndexRequired'] is False
assert report['newAgentRuntimeIntroduced'] is False
assert report['phase4KnowledgeGovernanceRetained'] is True
assert report['knowledgeMayCreateSystemFact'] is False
print('V25_PHASE5_RAG_RUNTIME_GATE=PASS')
print('V25_PHASE5_JAVA_RELEASE_GATE=PASS')
print('evidenceHash=' + runtime['evidenceHash'])
print('verificationHash=' + report['verificationHash'])
PY
