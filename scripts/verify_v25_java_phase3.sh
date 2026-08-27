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

"$PYTHON_BIN" scripts/verify_v25_knowledge_runtime_projection.py \
  --root "$ROOT_DIR" \
  --output "$OUT_DIR/knowledge-runtime-projection-verification.json"

"$PYTHON_BIN" scripts/verify_v25_agent_input_ingress.py \
  --root "$ROOT_DIR" \
  --output "$OUT_DIR/agent-input-ingress-verification.json"

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
projection = json.loads(Path("dist/v25-phase3/knowledge-runtime-projection-verification.json").read_text(encoding="utf-8"))
ingress = json.loads(Path("dist/v25-phase3/agent-input-ingress-verification.json").read_text(encoding="utf-8"))
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
assert projection["verified"] is True
assert projection["fieldProjectionExact"] is True
assert projection["compositionProjectionExact"] is True
assert projection["registeredFieldCount"] == 18
assert projection["registeredCompositionCount"] == 3
assert projection["governanceFilesystemReadRequiredAtRuntime"] is False
assert projection["runtimePackageGovernanceExpansionRequired"] is False
assert projection["bootstrapProjectionBeforeKnowledgeIngress"] is True
assert ingress["verified"] is True
assert ingress["artifactKnowledgeIngressRequired"] is True
assert ingress["preV25AgentInputReuseAllowed"] is False
assert ingress["agent1ArtifactCarriesUnifiedKnowledge"] is True
assert ingress["agent2ArtifactCarriesUnifiedKnowledge"] is True
assert ingress["runtimeGuardrailsSeparatedFromKnowledge"] is True
assert ingress["agent1LegacyExperiencePayloadBlocked"] is True
assert ingress["agent2LegacyRagPayloadBlocked"] is True
assert ingress["knowledgeEnvelopeHashRequired"] is True
assert ingress["knowledgeCompositionHashRequired"] is True
assert ingress["tokenRuntimeEntrypointsReplaced"] is False
assert ingress["promptBuildersConsumeArtifactKnowledge"] is True
print("V25_PHASE3_AGENT_KNOWLEDGE_GATE=PASS")
print("V25_PHASE3_RUNTIME_PROJECTION_GATE=PASS")
print("V25_PHASE3_ARTIFACT_INGRESS_GATE=PASS")
print("verificationHash=" + report["verificationHash"])
PY
