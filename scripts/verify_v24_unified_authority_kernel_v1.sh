#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v24-unified-authority-kernel-v1"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

JAVA_HOME_BIN="${JAVA_HOME:+$JAVA_HOME/bin}"
resolve_javac() {
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

JAVAC="$(resolve_javac || true)"
[ -n "$JAVAC" ] || { echo "JDK 17+ javac is required." >&2; exit 2; }
JAVA="${JAVA_BIN:-$(dirname "$JAVAC")/java}"
[ -x "$JAVA" ] || JAVA="$(command -v java || true)"
[ -n "$JAVA" ] || { echo "Java runtime is required." >&2; exit 2; }

JAVA_MAJOR="$($JAVA -version 2>&1 | sed -n '1s/.*version "\([0-9][0-9]*\).*/\1/p')"
[ -n "$JAVA_MAJOR" ] && [ "$JAVA_MAJOR" -ge 17 ] || {
  echo "JDK 17+ required; resolved java major=${JAVA_MAJOR:-unknown}." >&2
  exit 2
}

"$PYTHON_BIN" -m json.tool governance/v24/unified-authority-kernel-policy-v1.json >/dev/null

mapfile -t JAVA_SOURCES < <(find java-control-plane/src/main/java -type f -name '*.java' | sort)
[ "${#JAVA_SOURCES[@]}" -gt 0 ] || { echo "No Java control-plane sources found." >&2; exit 2; }
"$JAVAC" --release 17 -encoding UTF-8 -d "$CLASS_DIR" "${JAVA_SOURCES[@]}"

"$JAVA" -cp "$CLASS_DIR" com.zcentury.v24.UnifiedAuthorityKernelMain \
  --policy governance/v24/unified-authority-kernel-policy-v1.json \
  --output "$OUT_DIR/verification-report.json" \
  | tee "$OUT_DIR/summary.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

report = json.loads(Path('dist/v24-unified-authority-kernel-v1/verification-report.json').read_text(encoding='utf-8'))
assert report['schema'] == 'v24.unified_authority_kernel.verification.v1'
assert report['version'] == '24.23.0'
assert report['verified'] is True
assert report['enforcementMode'] == 'SHADOW'
assert report['testCount'] >= 10
assert report['taskScopedAuthorityVerified'] is True
assert report['effectiveAuthoritySubsetInvariantVerified'] is True
assert report['informationAuthorityVerified'] is True
assert report['invocationAuthorityVerified'] is True
assert report['temporalAuthorityVerified'] is True
assert report['mutationAuthorityVerified'] is True
assert report['derivedInformationFactPromotionBlocked'] is True
assert report['unregisteredInvocationEdgeBlocked'] is True
assert report['terminalReopenBlocked'] is True
assert report['terminalMutationBlocked'] is True
assert report['staleGenerationBlocked'] is True
assert report['modelAuthorityGrantBlocked'] is True
assert report['singleAuthorityGenerationRootRequired'] is True
assert report['authorityGrantCreated'] is False
assert report['productionAuthorityOwnershipChanged'] is False
assert report['existingAuthorityAdaptersReplaced'] is False
assert str(report['policyHash']).startswith('sha256:')
assert str(report['adapterRegistryHash']).startswith('sha256:')
assert str(report['verificationHash']).startswith('sha256:')

for decision in report['decisions']:
    assert decision['authorityGrantCreated'] is False
    assert decision['productionAuthorityOwnershipChanged'] is False
    assert decision['modelMayExpandAuthority'] is False
    assert str(decision['decisionHash']).startswith('sha256:')
    assert str(decision['requestHash']).startswith('sha256:')

print('V24_UNIFIED_AUTHORITY_KERNEL_V1=PASS')
print('policyHash=' + report['policyHash'])
print('adapterRegistryHash=' + report['adapterRegistryHash'])
print('verificationHash=' + report['verificationHash'])
PY
