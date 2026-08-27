#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v24-java-phase5"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

"$PYTHON_BIN" scripts/export_v24_phase5_deployment_evidence.py \
  --output "$OUT_DIR/deployment-baseline-evidence.json"

find java-control-plane/src/main/java -name '*.java' -print0 \
  | sort -z \
  | xargs -0 javac -encoding UTF-8 -d "$CLASS_DIR"

java -cp "$CLASS_DIR" com.zcentury.v24.Phase5Main \
  --root "$ROOT_DIR" \
  --evidence "$OUT_DIR/deployment-baseline-evidence.json" \
  --policy governance/v24/phase5-deployment-authority-policy.json \
  --compatibility governance/v24/deployment-compatibility-contract-v24.json \
  --legacy governance/v24/legacy-removal-contract-v24.json \
  --output "$OUT_DIR/phase5-verification-report.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
report = json.loads(Path('dist/v24-java-phase5/phase5-verification-report.json').read_text(encoding='utf-8'))
assert report['verified'] is True
assert report['enforcementMode'] == 'SHADOW'
assert report['deploymentAuthority'] == 'JAVA_SHADOW_DEPLOYMENT_CAS_GENERATION'
assert report['compatibilityAuthority'] == 'JAVA_SHADOW_COMPATIBILITY_DEFAULT_DENY'
assert report['legacyRemovalAuthority'] == 'JAVA_SHADOW_LEGACY_RETIREMENT_NO_FALLBACK'
assert report['deploymentReadPure'] is True
assert report['deploymentCasSingleWinner'] is True
assert report['staleGenerationBlocked'] is True
assert report['incompatibleCandidateBlocked'] is True
assert report['explicitSchemaMigrationRequired'] is True
assert report['legacyPrematureRemovalBlocked'] is True
assert report['legacyRemovalAfterProof'] is True
assert report['automaticLegacyFallbackForbidden'] is True
assert report['productionDeploymentWriterUnchanged'] is True
assert report['javaProductionDeploymentCutoverEnabled'] is False
assert report['productionLegacyDeletionByJavaEnabled'] is False
assert report['shellLegacyRetirementStillProduction'] is True
assert report['existingProductionDeploymentAuthority'] == 'BASH_SYSTEMD_ROOT'
assert report['existingCompatibilityAuthority'] == 'BASH_PLUS_PYTHON_ASSERTIONS'
assert report['existingLegacyRetirementAuthority'] == 'BASH_PYTHON_RUNTIME_EXCLUSIVITY_GUARD'
assert report['existingCurrentSymlinkCutover'] is True
assert report['existingRollbackPresent'] is True
assert report['existingLegacyForbiddenPathRetirement'] is True
assert report['existingLegacyWorkingTreeRetirement'] is True
print('V24_PHASE5_DEPLOYMENT_GATE=PASS')
print('verificationHash=' + report['verificationHash'])
PY
