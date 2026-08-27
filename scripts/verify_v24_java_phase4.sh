#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT_DIR="dist/v24-java-phase4"
CLASS_DIR="$OUT_DIR/classes"
rm -rf "$OUT_DIR"
mkdir -p "$CLASS_DIR"

"$PYTHON_BIN" scripts/export_v24_phase4_frontend_evidence.py \
  --output "$OUT_DIR/frontend-baseline-evidence.json"

find java-control-plane/src/main/java -name '*.java' -print0 \
  | sort -z \
  | xargs -0 javac -encoding UTF-8 -d "$CLASS_DIR"

java -cp "$CLASS_DIR" com.zcentury.v24.Phase4Main \
  --root "$ROOT_DIR" \
  --evidence "$OUT_DIR/frontend-baseline-evidence.json" \
  --policy governance/v24/phase4-frontend-authority-policy.json \
  --contract governance/v24/frontend-view-contract-v24.json \
  --output "$OUT_DIR/phase4-verification-report.json"

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
report = json.loads(Path('dist/v24-java-phase4/phase4-verification-report.json').read_text(encoding='utf-8'))
assert report['verified'] is True
assert report['enforcementMode'] == 'SHADOW'
assert report['frontendAuthority'] == 'JAVA_SHADOW_VIEW_HEAD_MANIFEST_CAS'
assert report['sseAuthority'] == 'JAVA_SHADOW_VIEW_HEAD_CHANGED'
assert report['headReadPure'] is True
assert report['casSingleWinner'] is True
assert report['staleGenerationBlocked'] is True
assert report['duplicateSseSuppressed'] is True
assert report['changedModuleFetchIsolation'] is True
assert report['changedModuleFetchCount'] == 1
assert report['sseEventName'] == 'view-head-changed'
assert report['sseFrameValid'] is True
assert report['eventHeadManifestAligned'] is True
assert report['browserJsRuntimeUnchanged'] is True
assert report['pythonProductionViewWriteAuthorityUnchanged'] is True
assert report['javaProductionViewCutoverEnabled'] is False
assert report['networkSseCutoverEnabled'] is False
assert report['existingHeadGetMayMaterialize'] is True
assert report['existingBrowserImmutableHashCache'] is True
assert report['existingBrowserEventSourcePresent'] is False
print('V24_PHASE4_FRONTEND_GATE=PASS')
print('verificationHash=' + report['verificationHash'])
PY
