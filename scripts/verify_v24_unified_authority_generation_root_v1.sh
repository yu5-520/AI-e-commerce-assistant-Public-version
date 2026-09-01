#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/v24-unified-authority-generation-root-v1"
CLASSES="$OUT/classes"
POLICY="$ROOT/governance/v24/unified-authority-kernel-policy-v1.json"
ROOT_POLICY="$ROOT/governance/v24/unified-authority-generation-root-v1.json"
REPORT="$OUT/verification-report.json"

rm -rf "$OUT"
mkdir -p "$CLASSES"

mapfile -t JAVA_SOURCES < <(find "$ROOT/java-control-plane/src/main/java" -name '*.java' -type f | sort)
if [ "${#JAVA_SOURCES[@]}" -eq 0 ]; then
  echo "No Java sources found" >&2
  exit 1
fi

javac --release 17 -encoding UTF-8 -d "$CLASSES" "${JAVA_SOURCES[@]}"
java -cp "$CLASSES" com.zcentury.v24.UnifiedAuthorityGenerationRootMain "$POLICY" > "$REPORT"

python3 - "$REPORT" "$ROOT_POLICY" <<'PY'
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
policy = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))

assert report['schema'] == 'v24.unified_authority_generation_root.verification.v1'
assert report['version'] == '24.24.0'
assert report['verified'] is True
assert report['enforcementMode'] == 'SHADOW'
assert report['rootSource'] == 'AuthorityGenerationStore'
assert report['rootBoundConsumerCount'] == 4
assert report['allRootBound'] is True
assert report['allSameInitialGeneration'] is True
assert report['rootBoundConsumerRotationForbidden'] is True
assert report['durableRootRotated'] is True
assert report['consumersObservedPreparedGeneration'] is True
assert report['queueRejectedOldGeneration'] is True
assert report['kernelRejectedOldGeneration'] is True
assert report['freshKernelPass'] is True
assert report['rollbackRotatedAgain'] is True
assert report['consumersObservedRollbackGeneration'] is True
assert report['rollbackInvalidatedFreshRequest'] is True
assert report['legacyLocalRotationCannotChangeAuthorityRoot'] is True
assert report['productionMutationAllowed'] is False
assert report['productionAuthorityOwnershipChanged'] is False
assert report['authorityGrantCreated'] is False
assert report['existingAuthorityAdaptersReplaced'] is False

assert policy['version'] == '24.24.0'
assert policy['enforcementMode'] == 'SHADOW'
assert policy['root']['singleWriter'] is True
assert policy['root']['durable'] is True
assert policy['root']['domainMayRotateGeneration'] is False
assert policy['root']['modelMayRotateGeneration'] is False
assert set(policy['consumers']) == {'INFORMATION', 'INVOCATION', 'TEMPORAL', 'MUTATION'}
assert all(v['binding'] == 'ROOT_CONSUMER' for v in policy['consumers'].values())
assert policy['invariants']['staleConsumerFenceMustFailClosed'] is True
assert policy['invariants']['staleKernelRequestMustConflict'] is True
assert policy['invariants']['productionMutationAllowed'] is False
assert policy['invariants']['productionAuthorityOwnershipChangedByThisPhase'] is False

print(json.dumps({
    'verified': True,
    'version': report['version'],
    'rootSource': report['rootSource'],
    'rootBoundConsumerCount': report['rootBoundConsumerCount'],
    'verificationHash': report['verificationHash'],
}, ensure_ascii=False, sort_keys=True))
PY
