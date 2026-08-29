#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JRE="$ROOT_DIR/runtime/java/jre/bin/java"
JAR="$ROOT_DIR/runtime/java/v24-production-authority.jar"
CONTRACT="$ROOT_DIR/runtime/java/runtime-contract.json"
MANIFEST="$ROOT_DIR/release/release-manifest.json"
BOOTSTRAP_PYTHON="${AI_BOOTSTRAP_PYTHON:-${AI_RELEASE_PYTHON:-python3}}"

[ -x "$JRE" ] || { echo "sealed V24 Java runtime is missing" >&2; exit 1; }
[ -s "$JAR" ] || { echo "sealed V24 authority JAR is missing" >&2; exit 1; }
[ -s "$CONTRACT" ] || { echo "V24 Java runtime contract is missing" >&2; exit 1; }
[ -s "$MANIFEST" ] || { echo "release manifest is missing" >&2; exit 1; }

ROOT_DIR="$ROOT_DIR" "$BOOTSTRAP_PYTHON" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ["ROOT_DIR"]).resolve()
manifest = json.loads((root / "release/release-manifest.json").read_text(encoding="utf-8"))
contract = json.loads((root / "runtime/java/runtime-contract.json").read_text(encoding="utf-8"))
if contract.get("sourceCommit") != manifest.get("sourceCommit"):
    raise SystemExit("java runtime sourceCommit differs from release manifest")
jar = root / contract["jarPath"]
if hashlib.sha256(jar.read_bytes()).hexdigest() != contract.get("jarSha256"):
    raise SystemExit("java authority JAR hash mismatch")
if contract.get("enforcementMode") != "READY_NO_AUTHORITY":
    raise SystemExit("unexpected Java authority enforcement mode")
if contract.get("productionMutationAllowed") is not False:
    raise SystemExit("Java production mutation must remain disabled")
print(json.dumps({
    "schema": "v24.java-startup-admission.v1",
    "sourceCommit": manifest["sourceCommit"],
    "releaseHash": manifest["releaseHash"],
    "contractHash": contract["contractHash"],
    "admitted": True,
}, sort_keys=True))
PY

exec "$JRE"   -Xms64m   -Xmx256m   -XX:MaxMetaspaceSize=128m   -XX:ActiveProcessorCount=2   -jar "$JAR"
