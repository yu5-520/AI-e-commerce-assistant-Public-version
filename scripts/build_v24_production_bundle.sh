#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA_HOME_RESOLVED="${V24_JAVA_HOME:-${JAVA_HOME:-}}"
SOURCE_COMMIT="${V24_SOURCE_COMMIT:-}"
BUILD_ROOT="$ROOT_DIR/.build/v24-production-authority"
CLASS_ROOT="$BUILD_ROOT/classes"
RUNTIME_ROOT="$ROOT_DIR/runtime/java"
JAR_PATH="$RUNTIME_ROOT/v24-production-authority.jar"
JRE_ROOT="$RUNTIME_ROOT/jre"
CONTRACT_PATH="$RUNTIME_ROOT/runtime-contract.json"
BUILD_PYTHON="${V24_BUILD_PYTHON:-$ROOT_DIR/.venv/bin/python}"

fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

[ -n "$JAVA_HOME_RESOLVED" ] || fail "V24_JAVA_HOME or JAVA_HOME is required"
[ -x "$JAVA_HOME_RESOLVED/bin/java" ] || fail "java executable is missing"
[ -x "$JAVA_HOME_RESOLVED/bin/javac" ] || fail "javac executable is missing"
[ -x "$JAVA_HOME_RESOLVED/bin/jar" ] || fail "jar executable is missing"
[ -x "$JAVA_HOME_RESOLVED/bin/jlink" ] || fail "jlink executable is missing"
[ -x "$BUILD_PYTHON" ] || fail "V24_BUILD_PYTHON is not executable"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "V24_SOURCE_COMMIT must be an exact lowercase commit SHA"

JAVA_VERSION_OUTPUT="$("$JAVA_HOME_RESOLVED/bin/java" -version 2>&1)"
printf '%s\n' "$JAVA_VERSION_OUTPUT"
printf '%s\n' "$JAVA_VERSION_OUTPUT" | grep -F '17.0.20' >/dev/null || fail "exact Java 17.0.20 is required"
printf '%s\n' "$JAVA_VERSION_OUTPUT" | grep -F 'Temurin-17.0.20+8' >/dev/null || fail "exact Temurin 17.0.20+8 runtime is required"

rm -rf "$BUILD_ROOT" "$RUNTIME_ROOT"
mkdir -p "$CLASS_ROOT" "$RUNTIME_ROOT"

mapfile -d '' JAVA_SOURCES < <(
  find "$ROOT_DIR/java-control-plane/src/main/java" -type f -name '*.java' -print0 | sort -z
)
[ "${#JAVA_SOURCES[@]}" -gt 0 ] || fail "Java control-plane sources are missing"

"$JAVA_HOME_RESOLVED/bin/javac" --release 17 -encoding UTF-8 -d "$CLASS_ROOT" "${JAVA_SOURCES[@]}"
"$JAVA_HOME_RESOLVED/bin/jar" --create   --file "$JAR_PATH"   --main-class com.zcentury.v24.ProductionAuthorityMain   -C "$CLASS_ROOT" .

"$JAVA_HOME_RESOLVED/bin/jlink"   --add-modules java.base,java.logging,jdk.httpserver   --strip-debug   --no-header-files   --no-man-pages   --compress=2   --output "$JRE_ROOT"

"$JRE_ROOT/bin/java" -version 2>&1 | grep -F '17.0.20' >/dev/null
"$JRE_ROOT/bin/java" -cp "$JAR_PATH" com.zcentury.v24.Phase1Main --help >/dev/null 2>&1 || true

ROOT_DIR="$ROOT_DIR" SOURCE_COMMIT="$SOURCE_COMMIT" "$BUILD_PYTHON" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ["ROOT_DIR"]).resolve()
runtime = root / "runtime" / "java"
contract = runtime / "runtime-contract.json"
paths = sorted(
    path for path in runtime.rglob("*")
    if path.is_file() and path != contract
)
if not paths:
    raise SystemExit("java runtime file set is empty")

entries = []
tree = hashlib.sha256()
for path in paths:
    relative = path.relative_to(root).as_posix()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    entries.append({"path": relative, "sha256": digest, "size": path.stat().st_size})
    tree.update(relative.encode("utf-8"))
    tree.update(b"\0")
    tree.update(bytes.fromhex(digest))

jar_path = root / "runtime" / "java" / "v24-production-authority.jar"
payload = {
    "schema": "v24.java-runtime.contract.v1",
    "version": "24.21.0",
    "sourceCommit": os.environ["SOURCE_COMMIT"],
    "distribution": "Temurin",
    "javaRuntimeVersion": "17.0.20+8",
    "javaFeatureVersion": 17,
    "architecture": "x64",
    "mainClass": "com.zcentury.v24.ProductionAuthorityMain",
    "jarPath": jar_path.relative_to(root).as_posix(),
    "jarSha256": hashlib.sha256(jar_path.read_bytes()).hexdigest(),
    "runtimeTreeSha256": tree.hexdigest(),
    "runtimeFileCount": len(entries),
    "runtimeFiles": entries,
    "enforcementMode": "READY_NO_AUTHORITY",
    "productionMutationAllowed": False,
}
material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
payload["contractHash"] = "sha256:" + hashlib.sha256(material).hexdigest()
contract.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "schema": payload["schema"],
    "sourceCommit": payload["sourceCommit"],
    "jarSha256": payload["jarSha256"],
    "runtimeTreeSha256": payload["runtimeTreeSha256"],
    "runtimeFileCount": payload["runtimeFileCount"],
    "contractHash": payload["contractHash"],
}, sort_keys=True))
PY

printf 'V24_PRODUCTION_BUNDLE_BUILD=PASS\n'
