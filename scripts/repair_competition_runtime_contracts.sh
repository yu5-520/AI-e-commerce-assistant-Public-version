#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "============================================================"
echo " Competition Runtime Contract-Lineage One-Click Verification"
echo "============================================================"
echo "root=$ROOT"
echo "virtualenv=disabled"

# Never create/activate/install a venv. Prefer the existing ECS/system Python only.
if [ -n "${PYTHON_BIN:-}" ] && [ -x "${PYTHON_BIN}" ]; then
  PY="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "ERROR: no existing system/ECS Python found" >&2
  exit 1
fi

"$PY" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ required; existing runtime is too old")
print("python=" + sys.executable)
print("version=" + sys.version.split()[0])
PY

echo
echo "[1/4] Syntax-check repaired Python files"
"$PY" -m py_compile \
  src/services/runtime_contract_guard_v1_service.py \
  src/services/agent3_semantic_path_repair_v1_service.py \
  src/services/agent_token_runtime_v225_service.py \
  src/services/agent_runtime_contract_v225_service.py \
  src/services/artifact_transport_service.py \
  scripts/verify_runtime_contract_lineage.py \
  scripts/compile_competition_lineage.py

echo
echo "[2/4] Verify unified field/interface ownership"
"$PY" scripts/verify_runtime_contract_lineage.py

echo
echo "[3/4] Compile registry -> hash-lineage evidence"
rm -rf dist/competition-contract-lineage
"$PY" scripts/compile_competition_lineage.py \
  --output-dir dist/competition-contract-lineage

echo
echo "[4/4] Assert compiled evidence is fail-closed and clean"
"$PY" - <<'PY'
import json
from pathlib import Path
root = Path("dist/competition-contract-lineage")
report = json.loads((root / "verification-report.json").read_text(encoding="utf-8"))
if report.get("verified") is not True:
    raise SystemExit("competition lineage verification failed: " + repr(report.get("findings")))
graph = json.loads((root / "lineage-graph.json").read_text(encoding="utf-8"))
print("verified=true")
print("runtimeHash=" + str(report.get("runtimeHash")))
print("graphHash=" + str(report.get("graphHash")))
print("runtimeFileCount=" + str(report.get("runtimeFileCount")))
print("lineageEdgeCount=" + str(len(graph.get("edges") or [])))
PY

echo
echo "============================================================"
echo " PASS: registry -> hash lineage -> contract repair verified"
echo " No virtual environment was created, activated, or installed."
echo "============================================================"
