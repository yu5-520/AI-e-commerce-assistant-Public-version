#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/dist/v24-unified-authority-domain-root-wiring-v1"
CLASS_DIR="$ROOT_DIR/dist/v24-unified-authority-domain-root-wiring-v1-classes"
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
[ -n "$JAVAC" ] || { echo "JDK 17+ javac is required for V24.25 verification." >&2; exit 2; }
JAVA="${JAVA_BIN:-$(dirname "$JAVAC")/java}"
[ -x "$JAVA" ] || JAVA="$(command -v java || true)"
[ -n "$JAVA" ] || { echo "Java runtime is required for V24.25 verification." >&2; exit 2; }

cd "$ROOT_DIR"
rm -rf "$OUT_DIR" "$CLASS_DIR"
mkdir -p "$OUT_DIR" "$CLASS_DIR"

# Preserve the existing Python -> Java mirror proof before adding the Root-bound wrapper.
# This validates that the legacy deterministic authority semantics still reproduce the
# production Python evidence and that Python production write authority is unchanged.
bash scripts/verify_v24_java_phase2.sh | tee "$OUT_DIR/python-java-phase2-gate.log"

mapfile -t JAVA_SOURCES < <(find java-control-plane/src/main/java -type f -name '*.java' | sort)
[ "${#JAVA_SOURCES[@]}" -gt 0 ] || { echo "No Java control-plane sources found." >&2; exit 2; }
"$JAVAC" --release 17 -encoding UTF-8 -d "$CLASS_DIR" "${JAVA_SOURCES[@]}"

"$JAVA" -cp "$CLASS_DIR" com.zcentury.v24.AuthorityDomainRootWiringMain \
  governance/v24/unified-authority-kernel-policy-v1.json \
  governance/v24/unified-authority-domain-root-binding-v1.json \
  | tee "$OUT_DIR/root-wiring-report.json"

"$PYTHON_BIN" - <<'PY'
import hashlib
import json
from pathlib import Path

out = Path('dist/v24-unified-authority-domain-root-wiring-v1')
phase2 = json.loads(Path('dist/v24-java-phase2/phase2-verification-report.json').read_text(encoding='utf-8'))
wiring = json.loads((out / 'root-wiring-report.json').read_text(encoding='utf-8'))

assert phase2['verified'] is True, phase2
assert phase2['enforcementMode'] == 'SHADOW', phase2
assert phase2['pythonProductionWriteAuthorityUnchanged'] is True, phase2
assert phase2['postgreSqlSourceOfTruthEnabled'] is False, phase2

assert wiring['verified'] is True, wiring
assert wiring['enforcementMode'] == 'SHADOW', wiring
assert wiring['allDomainsRootBound'] is True, wiring
assert wiring['legacyDeterministicSemanticParity'] is True, wiring
assert wiring['allOldDomainTokensFailClosed'] is True, wiring
assert wiring['freshRootBoundOperationsPass'] is True, wiring
assert wiring['preparedGenerationInvalidAfterRollback'] is True, wiring
assert wiring['productionOwnerBoundaryStable'] is True, wiring
assert wiring['productionMutationAllowed'] is False, wiring
assert wiring['productionAuthorityOwnershipChanged'] is False, wiring
assert wiring['authorityGrantCreated'] is False, wiring
assert wiring['externalProductionMirrorRequiredBeforeCutover'] is True, wiring
assert wiring['externalProductionMirrorParityProvenByThisPhase'] is False, wiring
assert wiring['cutoverAllowed'] is False, wiring
assert wiring['cutoverPrepareStatus'] == 'ROOT_WIRING_VERIFIED_EXTERNAL_MIRROR_REQUIRED', wiring

combined = {
    'schema': 'v24.unified_authority_domain_root_wiring.cutover_prepare_evidence.v1',
    'version': wiring['version'],
    'verified': True,
    'enforcementMode': 'SHADOW',
    'rootSource': wiring['rootSource'],
    'domainCount': wiring['domainCount'],
    'allDomainsRootBound': wiring['allDomainsRootBound'],
    'legacyDeterministicSemanticParity': wiring['legacyDeterministicSemanticParity'],
    'pythonJavaMirrorParityProven': True,
    'pythonJavaMirrorVerificationHash': phase2['verificationHash'],
    'pythonProductionWriteAuthorityUnchanged': phase2['pythonProductionWriteAuthorityUnchanged'],
    'allOldDomainTokensFailClosed': wiring['allOldDomainTokensFailClosed'],
    'freshRootBoundOperationsPass': wiring['freshRootBoundOperationsPass'],
    'productionOwnerBoundaryStable': wiring['productionOwnerBoundaryStable'],
    'productionMutationAllowed': wiring['productionMutationAllowed'],
    'productionAuthorityOwnershipChanged': wiring['productionAuthorityOwnershipChanged'],
    'authorityGrantCreated': wiring['authorityGrantCreated'],
    'externalProductionMirrorRequiredBeforeCutover': True,
    'externalProductionMirrorParityProven': False,
    'cutoverAllowed': False,
    'cutoverPrepareStatus': wiring['cutoverPrepareStatus'],
    'nextRequiredGate': wiring['nextRequiredGate'],
    'rootWiringVerificationHash': wiring['verificationHash'],
}
canonical = json.dumps(combined, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
combined['evidenceHash'] = 'sha256:' + hashlib.sha256(canonical).hexdigest()
(out / 'cutover-prepare-evidence.json').write_text(
    json.dumps(combined, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n',
    encoding='utf-8',
)
print('V24_25_DOMAIN_ROOT_WIRING_GATE=PASS')
print('evidenceHash=' + combined['evidenceHash'])
PY
