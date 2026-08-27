#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v25-phase4"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

"$PYTHON_BIN" scripts/verify_v25_phase4_knowledge_runtime.py \
  --output "$OUT_DIR/knowledge-runtime-verification.json"

find java-control-plane/src/main/java -name '*.java' -print0 \
  | sort -z \
  | xargs -0 javac -encoding UTF-8 -d "$CLASS_DIR"

java -cp "$CLASS_DIR" com.zcentury.v24.V25Phase4Main \
  --root "$ROOT_DIR" \
  --evidence "$OUT_DIR/knowledge-runtime-verification.json" \
  --policy governance/v25/phase4-knowledge-asset-governance-policy.json \
  --index-contract governance/v25/knowledge-index-contract-v25.json \
  --output "$OUT_DIR/phase4-verification-report.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

runtime = json.loads(Path('dist/v25-phase4/knowledge-runtime-verification.json').read_text(encoding='utf-8'))
report = json.loads(Path('dist/v25-phase4/phase4-verification-report.json').read_text(encoding='utf-8'))
assert runtime['verified'] is True
assert runtime['pendingReviewRetrievalBlocked'] is True
assert runtime['humanApprovalPromotesImmutableRevision'] is True
assert runtime['newRevisionSupersedesOldRevision'] is True
assert runtime['oldRevisionPreserved'] is True
assert runtime['indexManifestRotatesOnKnowledgeMutation'] is True
assert runtime['retrievalReceiptBindsRevisionAndManifest'] is True
assert runtime['headRollbackExact'] is True
assert runtime['headRollbackPinnedAcrossRetrieval'] is True
assert runtime['expiredKnowledgeBecomesStale'] is True
assert runtime['staleKnowledgeRetrievalBlocked'] is True
assert runtime['automaticApprovalAllowed'] is False
assert runtime['automaticDeleteAllowed'] is False
assert runtime['physicalRagProviderReplaced'] is False
assert runtime['newAgentRuntimeIntroduced'] is False
assert report['verified'] is True
assert report['enforcementMode'] == 'VERSIONED_KNOWLEDGE_ASSET_GOVERNANCE'
assert report['physicalRagProviderReplaced'] is False
assert report['newAgentRuntimeIntroduced'] is False
assert report['vectorIndexRequired'] is False
print('V25_PHASE4_KNOWLEDGE_RUNTIME_GATE=PASS')
print('V25_PHASE4_JAVA_RELEASE_GATE=PASS')
print('verificationHash=' + report['verificationHash'])
PY
