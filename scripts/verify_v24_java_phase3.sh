#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
JAVA_HOME_BIN="${JAVA_HOME:+$JAVA_HOME/bin}"

resolve_java() {
  local candidate
  for candidate in \
    "${JAVAC_BIN:-}" \
    "${JAVA_HOME_BIN:+$JAVA_HOME_BIN/javac}" \
    javac \
    /usr/lib/jvm/java-17-openjdk-amd64/bin/javac \
    /usr/lib/jvm/java-17-openjdk/bin/javac; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

JAVAC="$(resolve_java || true)"
[ -n "$JAVAC" ] || { echo "JDK 17+ javac is required for V24 phase3 verification." >&2; exit 2; }
JAVA="${JAVA_BIN:-$(dirname "$JAVAC")/java}"
[ -x "$JAVA" ] || JAVA="$(command -v java || true)"
[ -n "$JAVA" ] || { echo "Java runtime is required for V24 phase3 verification." >&2; exit 2; }

cd "$ROOT_DIR"
rm -rf dist/v24-java-phase3 dist/v24-java-classes-phase3
mkdir -p dist/v24-java-phase3 dist/v24-java-classes-phase3

"$PYTHON_BIN" -m py_compile scripts/export_v24_phase3_queue_evidence.py
"$PYTHON_BIN" scripts/export_v24_phase3_queue_evidence.py \
  --output dist/v24-java-phase3/python-queue-baseline.json \
  | tee dist/v24-java-phase3/python-queue-summary.json

mapfile -t JAVA_SOURCES < <(find java-control-plane/src/main/java -type f -name '*.java' | sort)
[ "${#JAVA_SOURCES[@]}" -gt 0 ] || { echo "No Java control-plane sources found." >&2; exit 2; }
"$JAVAC" --release 17 -encoding UTF-8 -d dist/v24-java-classes-phase3 "${JAVA_SOURCES[@]}"

"$JAVA" -cp dist/v24-java-classes-phase3 com.zcentury.v24.Phase1Main self-test
"$JAVA" -cp dist/v24-java-classes-phase3 com.zcentury.v24.Phase3Main \
  --root "$ROOT_DIR" \
  --evidence dist/v24-java-phase3/python-queue-baseline.json \
  --policy governance/v24/phase3-queue-authority-policy.json \
  --sql governance/v24/phase3-postgresql-queue-schema.sql \
  --output dist/v24-java-phase3/phase3-verification-report.json \
  | tee dist/v24-java-phase3/java-phase3-summary.json

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

root = Path('dist/v24-java-phase3')
evidence = json.loads((root / 'python-queue-baseline.json').read_text(encoding='utf-8'))
report = json.loads((root / 'phase3-verification-report.json').read_text(encoding='utf-8'))
assert report['verified'] is True, report
assert report['enforcementMode'] == 'SHADOW', report
assert report['queueSplitAuthority'] == 'JAVA_SHADOW_STATE_JOB_ARTIFACT_OUTBOX_SEPARATED', report
assert report['agent1QueueAuthority'] == 'JAVA_SHADOW_STAGE_INDEPENDENT', report
assert report['agent2QueueAuthority'] == 'JAVA_SHADOW_STAGE_INDEPENDENT', report
assert report['agent3QueueAuthority'] == 'JAVA_SHADOW_STAGE_INDEPENDENT', report
assert report['idempotencyTest']['uniqueJobCount'] == 1, report
assert report['idempotencyTest']['duplicateSuppressedCount'] == 9, report
assert report['concurrentClaimTest']['winners'] == 1, report
assert report['leaseRecoveryTest']['recovered'] == 1, report
assert report['threeStageHandoffTest']['agent1ToAgent2'] is True, report
assert report['threeStageHandoffTest']['agent2ToAgent3'] is True, report
assert report['threeStageHandoffTest']['agent3ToComplete'] is True, report
assert report['pipelineFlowTest']['crossStagePipelineOverlap'] is True, report
assert report['pipelineFlowTest']['agent3BackpressureIsolatedFromAgent1'] is True, report
assert report['generationFencingTest']['staleCommitAccepted'] is False, report
assert report['generationFencingTest']['reason'] == 'STALE_GENERATION', report
assert report['pythonProductionQueueWriteAuthorityUnchanged'] is True, report
assert report['pythonAgentProviderAuthorityUnchanged'] is True, report
assert report['globalGenerationBarrierProductionStillEnabled'] is True, report
assert report['postgreSqlSourceOfTruthEnabled'] is False, report
assert report['javaProductionQueueCutoverEnabled'] is False, report
assert report['pythonEvidenceHash'] == evidence['evidenceHash'], report
print('V24_PHASE3_QUEUE_GATE=PASS')
print('verificationHash=' + report['verificationHash'])
PY
