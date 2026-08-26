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
[ -n "$JAVAC" ] || {
  echo "JDK 17+ javac is required for V24 phase1 verification." >&2
  exit 2
}
JAVA="${JAVA_BIN:-$(dirname "$JAVAC")/java}"
[ -x "$JAVA" ] || JAVA="$(command -v java || true)"
[ -n "$JAVA" ] || {
  echo "Java runtime is required for V24 phase1 verification." >&2
  exit 2
}

JAVA_MAJOR="$($JAVA -version 2>&1 | sed -n '1s/.*version "\([0-9][0-9]*\).*/\1/p')"
[ -n "$JAVA_MAJOR" ] && [ "$JAVA_MAJOR" -ge 17 ] || {
  echo "JDK 17+ required; resolved java major=${JAVA_MAJOR:-unknown}." >&2
  exit 2
}

cd "$ROOT_DIR"
rm -rf dist/v24-java-classes dist/v24-java-phase1 dist/v24-runtime-candidate dist/competition-lineage-v24
mkdir -p dist/v24-java-classes dist/v24-java-phase1

"$PYTHON_BIN" -m py_compile \
  scripts/compile_competition_lineage.py \
  scripts/competition_interface_gate.py

"$PYTHON_BIN" scripts/compile_competition_lineage.py \
  --source-commit "${V24_SOURCE_COMMIT:-${GITHUB_SHA:-unknown}}" \
  --output-dir dist/competition-lineage-v24

mapfile -t JAVA_SOURCES < <(find java-control-plane/src/main/java -type f -name '*.java' | sort)
[ "${#JAVA_SOURCES[@]}" -gt 0 ] || {
  echo "No Java control-plane sources found." >&2
  exit 2
}
"$JAVAC" --release 17 -encoding UTF-8 -d dist/v24-java-classes "${JAVA_SOURCES[@]}"

"$JAVA" -cp dist/v24-java-classes com.zcentury.v24.Phase1Main self-test
"$JAVA" -cp dist/v24-java-classes com.zcentury.v24.Phase1Main shadow-compile \
  --root "$ROOT_DIR" \
  --lineage-dir dist/competition-lineage-v24 \
  --output-dir dist/v24-java-phase1

mkdir -p dist/v24-runtime-candidate
"$PYTHON_BIN" - "$ROOT_DIR" \
  "$ROOT_DIR/dist/competition-lineage-v24/runtime-files.txt" \
  "$ROOT_DIR/dist/v24-runtime-candidate" <<'PY'
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
listing = Path(sys.argv[2]).resolve()
candidate = Path(sys.argv[3]).resolve()
for raw in listing.read_text(encoding="utf-8").splitlines():
    relative = Path(raw.strip())
    if not raw.strip():
        continue
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"unsafe_runtime_path:{raw}")
    source = (root / relative).resolve()
    if root != source and root not in source.parents:
        raise SystemExit(f"runtime_path_escapes_root:{raw}")
    if not source.is_file():
        raise SystemExit(f"runtime_file_missing:{raw}")
    target = candidate / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
PY

"$JAVA" -cp dist/v24-java-classes com.zcentury.v24.Phase1Main admit \
  --root "$ROOT_DIR" \
  --candidate dist/v24-runtime-candidate \
  --manifest dist/v24-java-phase1/active-runtime-manifest.json \
  --output dist/v24-java-phase1/runtime-admission-report.json

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

root = Path("dist/v24-java-phase1")
phase = json.loads((root / "phase1-verification-report.json").read_text(encoding="utf-8"))
admission = json.loads((root / "runtime-admission-report.json").read_text(encoding="utf-8"))
assert phase["verified"] is True, phase
assert phase["defaultRuntimeEligibility"] == "DENY", phase
assert phase["legacyOutsideActiveGraphRuntimeEligible"] is False, phase
assert phase["pythonRuntimeReplaced"] is False, phase
assert admission["verified"] is True, admission
assert admission["exactFileSetRequired"] is True, admission
assert admission["retiredOrUnregisteredRuntimeAllowed"] is False, admission
print("V24_PHASE1_SHADOW_GATE=PASS")
print("activeRuntimeGraphHash=" + phase["activeRuntimeGraphHash"])
print("activeRuntimeManifestHash=" + phase["activeRuntimeManifestHash"])
print("runtimeAdmissionHash=" + admission["admissionHash"])
PY
