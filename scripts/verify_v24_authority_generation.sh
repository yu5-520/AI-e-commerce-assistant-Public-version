#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_PYTHON="${V24_BUILD_PYTHON:-$ROOT_DIR/.venv/bin/python}"
SOURCE_COMMIT="${V24_SOURCE_COMMIT:?V24_SOURCE_COMMIT is required}"
JAVA_HOME_VALUE="${V24_JAVA_HOME:-${JAVA_HOME:-}}"
[ -n "$JAVA_HOME_VALUE" ] || { echo "V24_JAVA_HOME or JAVA_HOME is required" >&2; exit 1; }

V24_JAVA_HOME="$JAVA_HOME_VALUE" \
V24_BUILD_PYTHON="$BUILD_PYTHON" \
V24_SOURCE_COMMIT="$SOURCE_COMMIT" \
  bash "$ROOT_DIR/scripts/build_v24_production_bundle.sh"

JAVA="$ROOT_DIR/runtime/java/jre/bin/java"
JAR="$ROOT_DIR/runtime/java/v24-production-authority.jar"
[ -x "$JAVA" ] && [ -s "$JAR" ]

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/v24-authority-generation.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT INT TERM
export V24_AUTHORITY_STATE_PATH="$WORK_DIR/authority-generation.json"

RELEASE_HASH="sha256:$(printf '%064d' 2422)"
PROOF="$WORK_DIR/proof.json"
SOURCE_COMMIT="$SOURCE_COMMIT" RELEASE_HASH="$RELEASE_HASH" "$BUILD_PYTHON" - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "schema": "v24.authority-generation.proof.v1",
    "verified": True,
    "sourceCommit": os.environ["SOURCE_COMMIT"],
    "releaseHash": os.environ["RELEASE_HASH"],
    "gates": {
        "SEALED_JAVA_RUNTIME_VERIFIED": True,
        "JAVA_SERVICE_READY_NO_AUTHORITY": True,
        "PYTHON_JAVA_MIRROR_PARITY_PROVEN": True,
        "DURABLE_STATE_ADAPTER_VERIFIED": True,
        "SINGLE_WRITER_GENERATION_ROTATION_PREPARED": True,
        "FULL_ROLLBACK_PROVEN": True,
    },
}
Path(os.environ["PROOF"]).write_text(
    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

STATUS0="$WORK_DIR/status0.json"
PREPARED="$WORK_DIR/prepared.json"
RESTARTED="$WORK_DIR/restarted.json"
ROLLED_BACK="$WORK_DIR/rolled-back.json"

"$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain status > "$STATUS0"
STATE_HASH0="$("$BUILD_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["stateHash"])' "$STATUS0")"
GEN_HASH0="$("$BUILD_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["generationHash"])' "$STATUS0")"

"$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain \
  prepare "$STATE_HASH0" "$SOURCE_COMMIT" "$RELEASE_HASH" "$PROOF" > "$PREPARED"

# A separate JVM must recover exactly the durable prepared state.
"$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain status > "$RESTARTED"
cmp -s "$PREPARED" "$RESTARTED"

STATE_HASH1="$("$BUILD_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["stateHash"])' "$PREPARED")"
GEN_HASH1="$("$BUILD_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["generationHash"])' "$PREPARED")"
SEQ1="$("$BUILD_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["generationSeq"])' "$PREPARED")"
TOKEN1="$("$BUILD_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["fencingToken"])' "$PREPARED")"

"$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain matches "$SEQ1" "$GEN_HASH1" "$TOKEN1" \
  | "$BUILD_PYTHON" -c 'import json,sys; assert json.load(sys.stdin)["matched"] is True'
if "$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain matches 0 "$GEN_HASH0" 0 \
  | "$BUILD_PYTHON" -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin)["matched"] is False else 1)'; then
  :
else
  echo "stale generation unexpectedly matched" >&2
  exit 1
fi

# Compare-and-set must reject the pre-rotation state hash.
if "$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain \
    prepare "$STATE_HASH0" "$SOURCE_COMMIT" "$RELEASE_HASH" "$PROOF" >/dev/null 2>&1; then
  echo "stale authority state CAS unexpectedly passed" >&2
  exit 1
fi

# V24.22 cannot activate production authority under any input.
if "$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain activate >/dev/null 2>&1; then
  echo "five-authority activation unexpectedly enabled" >&2
  exit 1
fi

cp "$V24_AUTHORITY_STATE_PATH" "$WORK_DIR/good-state.json"
STATE_PATH="$V24_AUTHORITY_STATE_PATH" "$BUILD_PYTHON" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["STATE_PATH"])
value = json.loads(path.read_text(encoding="utf-8"))
value["reason"] = "tampered_without_state_hash_rotation"
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
if "$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain status >/dev/null 2>&1; then
  echo "tampered authority state unexpectedly passed" >&2
  exit 1
fi
cp "$WORK_DIR/good-state.json" "$V24_AUTHORITY_STATE_PATH"

"$JAVA" -cp "$JAR" com.zcentury.v24.AuthorityGenerationMain \
  rollback "$STATE_HASH1" "ci_rollback_proof" > "$ROLLED_BACK"

SOURCE_COMMIT="$SOURCE_COMMIT" RELEASE_HASH="$RELEASE_HASH" \
STATUS0="$STATUS0" PREPARED="$PREPARED" ROLLED_BACK="$ROLLED_BACK" \
"$BUILD_PYTHON" - <<'PY'
import json
import os
from pathlib import Path

def read(name):
    return json.loads(Path(os.environ[name]).read_text(encoding="utf-8"))

initial = read("STATUS0")
prepared = read("PREPARED")
rolled = read("ROLLED_BACK")
assert initial["mode"] == "READY_NO_AUTHORITY"
assert initial["generationSeq"] == 0
assert prepared["mode"] == "CUTOVER_PREPARED"
assert prepared["generationSeq"] == 1
assert prepared["fencingToken"] == 1
assert prepared["previousGenerationHash"] == initial["generationHash"]
assert prepared["sourceCommit"] == os.environ["SOURCE_COMMIT"]
assert prepared["releaseHash"] == os.environ["RELEASE_HASH"]
assert prepared["productionMutationAllowed"] is False
assert "JAVA_PRODUCTION" not in set(prepared["owners"].values())
assert rolled["mode"] == "READY_NO_AUTHORITY"
assert rolled["generationSeq"] == 2
assert rolled["fencingToken"] == 2
assert rolled["previousGenerationHash"] == prepared["generationHash"]
assert rolled["productionMutationAllowed"] is False
assert rolled["deploymentAuthorityTransferAllowed"] is False
assert rolled["legacyRemovalAllowed"] is False
print(json.dumps({
    "schema": "v24.authority-generation.verification.v1",
    "verified": True,
    "sourceCommit": os.environ["SOURCE_COMMIT"],
    "preparedGenerationHash": prepared["generationHash"],
    "rollbackGenerationHash": rolled["generationHash"],
    "staleGenerationRejected": True,
    "staleStateCasRejected": True,
    "tamperRejected": True,
    "restartRecoveryVerified": True,
    "activationStillForbidden": True,
}, sort_keys=True))
PY

printf 'V24_AUTHORITY_GENERATION_GATE=PASS\n'
