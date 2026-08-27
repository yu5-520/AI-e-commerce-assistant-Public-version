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
[ -n "$JAVAC" ] || { echo "JDK 17+ javac is required for V24 phase2 verification." >&2; exit 2; }
JAVA="${JAVA_BIN:-$(dirname "$JAVAC")/java}"
[ -x "$JAVA" ] || JAVA="$(command -v java || true)"
[ -n "$JAVA" ] || { echo "Java runtime is required for V24 phase2 verification." >&2; exit 2; }

cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
rm -rf dist/v24-java-phase2 dist/v24-java-classes-phase2
mkdir -p dist/v24-java-phase2 dist/v24-java-classes-phase2

"$PYTHON_BIN" -m py_compile scripts/export_v24_phase2_shadow_evidence.py
"$PYTHON_BIN" scripts/export_v24_phase2_shadow_evidence.py \
  --output dist/v24-java-phase2/python-shadow-evidence.json \
  | tee dist/v24-java-phase2/python-shadow-summary.json

mapfile -t JAVA_SOURCES < <(find java-control-plane/src/main/java -type f -name '*.java' | sort)
[ "${#JAVA_SOURCES[@]}" -gt 0 ] || { echo "No Java control-plane sources found." >&2; exit 2; }
"$JAVAC" --release 17 -encoding UTF-8 -d dist/v24-java-classes-phase2 "${JAVA_SOURCES[@]}"

"$JAVA" -cp dist/v24-java-classes-phase2 com.zcentury.v24.Phase1Main self-test
"$JAVA" -cp dist/v24-java-classes-phase2 com.zcentury.v24.Phase2Main \
  --root "$ROOT_DIR" \
  --evidence dist/v24-java-phase2/python-shadow-evidence.json \
  --policy governance/v24/phase2-authority-policy.json \
  --gates governance/v24/unified-gate-definitions.json \
  --output dist/v24-java-phase2/phase2-verification-report.json \
  | tee dist/v24-java-phase2/java-phase2-summary.json

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

root = Path('dist/v24-java-phase2')
evidence = json.loads((root / 'python-shadow-evidence.json').read_text(encoding='utf-8'))
report = json.loads((root / 'phase2-verification-report.json').read_text(encoding='utf-8'))
assert report['verified'] is True, report
assert report['enforcementMode'] == 'SHADOW', report
assert report['mappingVectorCount'] >= 3, report
assert report['gateVectorCount'] >= 7, report
assert report['taskStateVectorCount'] >= 6, report
assert report['mappingAuthority'] == 'JAVA_SHADOW_REPRODUCED', report
assert report['gateAuthority'] == 'JAVA_SHADOW_DECISION', report
assert report['taskStateAuthority'] == 'JAVA_SHADOW_DECISION_WITH_VERSION_CAS', report
assert report['unknownGate'] == 'BLOCK', report
assert report['unknownTaskState'] == 'BLOCK', report
assert report['terminalReopen'] == 'BLOCK', report
assert report['canonicalHashAlgorithm'] == 'SHA-256', report
assert report['pythonProductionWriteAuthorityUnchanged'] is True, report
assert report['postgreSqlSourceOfTruthEnabled'] is False, report
assert report['pythonEvidenceHash'] == evidence['evidenceHash'], report
print('V24_PHASE2_SHADOW_GATE=PASS')
print('verificationHash=' + report['verificationHash'])
PY
