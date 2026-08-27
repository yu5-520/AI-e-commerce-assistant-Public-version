#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v25-phase1"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

"$PYTHON_BIN" scripts/export_v25_phase1_knowledge_baseline.py \
  --output "$OUT_DIR/knowledge-baseline-evidence.json"

find java-control-plane/src/main/java -name '*.java' -print0 \
  | sort -z \
  | xargs -0 javac -encoding UTF-8 -d "$CLASS_DIR"

java -cp "$CLASS_DIR" com.zcentury.v24.V25Phase1Main \
  --root "$ROOT_DIR" \
  --evidence "$OUT_DIR/knowledge-baseline-evidence.json" \
  --policy governance/v25/phase1-knowledge-authority-policy.json \
  --baseline governance/v25/knowledge-baseline-v25.json \
  --fields governance/v25/rag-field-registry-v25.json \
  --domains governance/v25/knowledge-distribution-domains-v25.json \
  --output "$OUT_DIR/phase1-verification-report.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("dist/v25-phase1/phase1-verification-report.json").read_text(encoding="utf-8"))
assert report["verified"] is True
assert report["enforcementMode"] == "SHADOW"
assert report["knowledgeBaselineAuthority"] == "JAVA_SHADOW_KNOWLEDGE_INVENTORY"
assert report["ragFieldRegistryAuthority"] == "JAVA_SHADOW_UNIFIED_RAG_FIELD_REGISTRY"
assert report["knowledgeDomainAuthority"] == "JAVA_SHADOW_DISTRIBUTION_DOMAIN_RESOLUTION"
assert report["inventorySourceCount"] >= 7
assert report["registeredKnowledgeFieldCount"] >= 18
assert report["distributionDomainCount"] >= 11
assert report["fieldToDomainResolutionVerified"] is True
assert report["crossDomainFieldResolutionVerified"] is True
assert report["unknownKnowledgeFieldBlocked"] is True
assert report["systemContractLeakBlocked"] is True
assert report["onePhysicalKnowledgeStore"] is True
assert report["fieldFirst"] is True
assert report["distributionDomainBeforeVector"] is True
assert report["productionAgentInputsUnchanged"] is True
assert report["productionRagWriterUnchanged"] is True
assert report["vectorRetrievalCutoverEnabled"] is False
print("V25_PHASE1_KNOWLEDGE_GATE=PASS")
print("verificationHash=" + report["verificationHash"])
PY
