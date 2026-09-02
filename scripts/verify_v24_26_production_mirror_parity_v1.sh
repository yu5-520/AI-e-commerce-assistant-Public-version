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
[ -n "$JAVAC" ] || { echo "JDK 17+ javac is required for V24.26 verification." >&2; exit 2; }
JAVA="${JAVA_BIN:-$(dirname "$JAVAC")/java}"
[ -x "$JAVA" ] || JAVA="$(command -v java || true)"
[ -n "$JAVA" ] || { echo "Java runtime is required for V24.26 verification." >&2; exit 2; }

cd "$ROOT_DIR"
rm -rf dist/v24-production-mirror-parity-v1 dist/v24-java-classes-v24-26
mkdir -p dist/v24-production-mirror-parity-v1 dist/v24-java-classes-v24-26

mapfile -t JAVA_SOURCES < <(find java-control-plane/src/main/java -type f -name '*.java' | sort)
[ "${#JAVA_SOURCES[@]}" -gt 0 ] || { echo "No Java control-plane sources found." >&2; exit 2; }
"$JAVAC" --release 17 -encoding UTF-8 -d dist/v24-java-classes-v24-26 "${JAVA_SOURCES[@]}"

ARGS=(
  governance/v24/unified-authority-kernel-policy-v1.json
  governance/v24/production-mirror-parity-policy-v1.json
)
if [ -n "${V24_26_EXTERNAL_MIRROR_EVIDENCE:-}" ]; then
  ARGS+=("$V24_26_EXTERNAL_MIRROR_EVIDENCE")
fi

"$JAVA" -cp dist/v24-java-classes-v24-26 com.zcentury.v24.ProductionMirrorParityMain "${ARGS[@]}" \
  | tee dist/v24-production-mirror-parity-v1/verification-report.json

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

report = json.loads(Path('dist/v24-production-mirror-parity-v1/verification-report.json').read_text(encoding='utf-8'))
assert report['verified'] is True, report
assert report['enforcementMode'] == 'SHADOW', report
assert report['rootSource'] == 'AuthorityGenerationStore', report
assert report['mirrorMechanismVerified'] is True, report
assert report['repositoryReplayMechanismOnly'] is True, report
assert report['replayWindowCount'] >= 2, report
assert report['staleGenerationBlocked'] is True, report
assert report['inFlightDrainVerified'] is True, report
assert report['freshGenerationAdmissible'] is True, report
assert report['rollbackWindowVerified'] is True, report
assert report['preparedGenerationInvalidAfterRollback'] is True, report
assert report['productionOwnerBoundaryStable'] is True, report
assert report['productionMutationAllowed'] is False, report
assert report['mismatchEvidenceFailClosed'] is True, report
assert report['productionAuthorityOwnershipChanged'] is False, report
assert report['authorityGrantCreated'] is False, report
assert report['cutoverAllowed'] is False, report
if report['externalEvidencePresent']:
    assert report['status'] in {
        'PRODUCTION_MIRROR_PARITY_PROVEN_OWNER_TRANSFER_GATE_REQUIRED',
        'MIRROR_MECHANISM_VERIFIED_EXTERNAL_EVIDENCE_REQUIRED',
    }, report
else:
    assert report['externalProductionMirrorParityProven'] is False, report
    assert report['cutoverQualificationReady'] is False, report
    assert report['status'] == 'MIRROR_MECHANISM_VERIFIED_EXTERNAL_EVIDENCE_REQUIRED', report
    assert report['nextRequiredGate'] == 'SEALED_EXTERNAL_PRODUCTION_MIRROR_RECEIPTS', report
print('V24_26_PRODUCTION_MIRROR_PARITY_ROLLBACK_WINDOW=PASS')
print('verificationHash=' + report['verificationHash'])
PY
