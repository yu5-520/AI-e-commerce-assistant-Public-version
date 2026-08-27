#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v25-phase2"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

"$PYTHON_BIN" scripts/export_v25_phase2_retrieval_evidence.py \
  --root "$ROOT_DIR" \
  --output "$OUT_DIR/retrieval-baseline-evidence.json"

find java-control-plane/src/main/java -name '*.java' -print0 \
  | sort -z \
  | xargs -0 javac -encoding UTF-8 -d "$CLASS_DIR"

java -cp "$CLASS_DIR" com.zcentury.v24.V25Phase2Main \
  --root "$ROOT_DIR" \
  --evidence "$OUT_DIR/retrieval-baseline-evidence.json" \
  --policy governance/v25/phase2-retrieval-authority-policy.json \
  --fields governance/v25/rag-field-registry-v25.json \
  --domains governance/v25/knowledge-distribution-domains-v25.json \
  --aliases governance/v25/rag-alias-registry-v25.json \
  --structured governance/v25/rag-structured-filter-contract-v25.json \
  --graph governance/v25/knowledge-graph-contract-v25.json \
  --output "$OUT_DIR/phase2-verification-report.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("dist/v25-phase2/phase2-verification-report.json").read_text(encoding="utf-8"))
assert report["verified"] is True
assert report["enforcementMode"] == "SHADOW"
assert report["retrievalAuthority"] == "JAVA_SHADOW_FIELD_FIRST_RETRIEVAL"
assert report["fieldDirectAuthority"] == "JAVA_SHADOW_EXACT_FIELD"
assert report["aliasStructuredAuthority"] == "JAVA_SHADOW_ALIAS_STRUCTURED"
assert report["vectorGraphAuthority"] == "JAVA_SHADOW_SUPPLEMENT_ADMISSION"
assert report["registeredAliasCount"] >= 50
assert report["structuredFilterKeyCount"] >= 8
assert report["graphEdgeTypeCount"] >= 4
assert report["exactStopsSemanticSearch"] is True
assert report["structuredStopsSemanticSearch"] is True
assert report["aliasCanonicalizationVerified"] is True
assert report["vectorRunsOnlyAfterDeterministicLayers"] is True
assert report["vectorRouteProofRequired"] is True
assert report["graphRequiresVectorStage"] is True
assert report["graphSystemTargetBlocked"] is True
assert report["unknownAliasBlocked"] is True
assert report["unknownStructuredFilterBlocked"] is True
assert report["insufficientEvidenceExposed"] is True
assert report["retrievalMayCreateSystemFact"] is False
assert report["productionAgentInputsUnchanged"] is True
assert report["productionRagWriterUnchanged"] is True
assert report["productionRetrievalCutoverEnabled"] is False
print("V25_PHASE2_RETRIEVAL_GATE=PASS")
print("verificationHash=" + report["verificationHash"])
PY
