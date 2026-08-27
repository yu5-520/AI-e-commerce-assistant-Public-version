#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v25-phase3"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

"$PYTHON_BIN" scripts/export_v25_phase3_agent_knowledge_evidence.py \
  --root "$ROOT_DIR" \
  --output "$OUT_DIR/agent-knowledge-migration-evidence.json"

find java-control-plane/src/main/java -name '*.java' -print0 \
  | sort -z \
  | xargs -0 javac -encoding UTF-8 -d "$CLASS_DIR"

java -cp "$CLASS_DIR" com.zcentury.v24.V25Phase3Main \
  --root "$ROOT_DIR" \
  --evidence "$OUT_DIR/agent-knowledge-migration-evidence.json" \
  --policy governance/v25/phase3-agent-knowledge-migration-policy.json \
  --fields governance/v25/rag-field-registry-v25.json \
  --domains governance/v25/knowledge-distribution-domains-v25.json \
  --composition governance/v25/knowledge-composition-table-v25.json \
  --output "$OUT_DIR/phase3-verification-report.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("dist/v25-phase3/phase3-verification-report.json").read_text(encoding="utf-8"))
assert report["verified"] is True
assert report["enforcementMode"] == "PRODUCTION_KNOWLEDGE_INGRESS"
assert report["compositionAuthority"] == "JAVA_RELEASE_GATE"
assert report["productionKnowledgeIngress"] == "PYTHON_V25_UNIFIED"
assert report["registeredCompositionCount"] == 3
assert report["agent1KnowledgeMigrated"] is True
assert report["agent2KnowledgeMigrated"] is True
assert report["agent3KnowledgeMigrated"] is True
assert report["agent1LegacyExperienceDirectReadAllowed"] is False
assert report["legacyDirectAgentKnowledgeRead"] is False
assert report["legacyProviderBehindUnifiedAdapter"] is True
assert report["physicalRagProviderCutover"] is False
assert report["newAgent3RuntimeIntroduced"] is False
assert report["runtimeEntrypointsUnchanged"] is True
assert report["unknownAgentBlocked"] is True
assert report["unsupportedPredicateBlocked"] is True
assert report["consumerLeakBlocked"] is True
assert report["retrievalMayCreateSystemFact"] is False
assert report["insufficientEvidenceMustRemainVisible"] is True
print("V25_PHASE3_AGENT_KNOWLEDGE_GATE=PASS")
print("verificationHash=" + report["verificationHash"])
PY
