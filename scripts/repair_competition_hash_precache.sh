#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "============================================================"
echo " Competition Unified Registry -> Hash Lineage -> Precache"
echo "============================================================"

if [ -n "${PYTHON_BIN:-}" ] && [ -x "${PYTHON_BIN}" ]; then
  BASE_PY="$PYTHON_BIN"
elif [ -x /opt/python/3.11.9/bin/python3.11 ]; then
  BASE_PY=/opt/python/3.11.9/bin/python3.11
elif command -v python3 >/dev/null 2>&1; then
  BASE_PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  BASE_PY="$(command -v python)"
else
  echo "ERROR: existing Python runtime not found" >&2
  exit 1
fi

"$BASE_PY" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ required")
print("basePython=" + sys.executable)
print("baseVersion=" + sys.version.split()[0])
PY

echo
echo "[1/7] Verify existing unified Registry and Hash Lineage"
bash scripts/repair_competition_runtime_contracts.sh

echo
echo "[2/7] Prepare verified application runtime"
RUNTIME_DIR="$ROOT/dist/hash-precache-runtime"
mkdir -p "$RUNTIME_DIR"
chmod +x scripts/prepare_competition_runtime_venv.sh
RUNTIME_PYTHON="$(
  COMPETITION_VENV_ROOT="${COMPETITION_VENV_ROOT:-/opt/actions-runner-public/competition-runtime-venvs}" \
  COMPETITION_BASE_PYTHON="$BASE_PY" \
  bash scripts/prepare_competition_runtime_venv.sh \
    "$ROOT" "$RUNTIME_DIR" \
    | tee "$RUNTIME_DIR/runtime-prepare.log" | tail -n 1
)"
test -x "$RUNTIME_PYTHON"
"$RUNTIME_PYTHON" -c 'import fastapi, sqlalchemy, openpyxl'
echo "runtimePython=$RUNTIME_PYTHON"

echo
echo "[3/7] Compile semantic-precache runtime and verifier"
"$RUNTIME_PYTHON" -m py_compile \
  src/services/competition_hash_precache_registry_v1_service.py \
  src/services/competition_evidence_v215_runtime_service.py \
  scripts/verify_competition_hash_precache_registry.py \
  tests/test_competition_hash_precache_registry_v1.py

echo
echo "[4/7] Verify layered/classified unified precache registry"
"$RUNTIME_PYTHON" scripts/verify_competition_hash_precache_registry.py

echo
echo "[5/7] Run semantic/execution identity regression tests"
"$RUNTIME_PYTHON" -m unittest -q tests.test_competition_hash_precache_registry_v1

echo
echo "[6/7] Initialize cache index and warm active report prefixes H1/H2/H3 when available"
"$RUNTIME_PYTHON" - <<'PY'
import json

from src.repositories.sqlite_repository import connect
from src.services.competition_hash_precache_registry_v1_service import (
    ensure_hash_precache_tables,
    hash_precache_status,
)

ensure_hash_precache_tables()
with connect() as conn:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='imported_report_rows' LIMIT 1"
    ).fetchone()
    versions = []
    if exists:
        rows = conn.execute(
            """
            SELECT data_version, MIN(rowid) AS first_rowid
            FROM imported_report_rows
            WHERE data_version IS NOT NULL AND TRIM(data_version)!=''
            GROUP BY data_version
            ORDER BY first_rowid ASC
            LIMIT 3
            """
        ).fetchall()
        versions = [str(row["data_version"]) for row in rows if row["data_version"]]

receipts = []
if versions:
    from src.services.v22_runtime_service import install_v22_runtime
    install_v22_runtime()
    from src.services import product_signal_snapshot_service as signal_snapshot
    for index, version in enumerate(versions, start=1):
        result = signal_snapshot.materialize_product_signal_snapshot(
            data_version=version,
            force=False,
        )
        receipts.append(
            {
                "prefix": f"H{index}",
                "dataVersion": version,
                "normalizedReportHash": result.get("normalizedReportHash"),
                "inputSequenceHash": result.get("inputSequenceHash"),
                "preAgentComputeHash": result.get("preAgentComputeHash"),
                "semanticCacheHit": result.get("semanticCacheHit"),
                "preAgentCacheStatus": result.get("preAgentCacheStatus"),
                "evidenceInputHash": result.get("evidenceInputHash"),
            }
        )
print(json.dumps({
    "activePrefixVersions": versions,
    "warmReceipts": receipts,
    "status": hash_precache_status(),
}, ensure_ascii=False, sort_keys=True))
PY

echo
echo "[7/7] Assert strict/semantic routing contract"
"$RUNTIME_PYTHON" - <<'PY'
import json
from pathlib import Path
registry = json.loads(Path("config/competition_hash_precache_registry_v1.json").read_text(encoding="utf-8"))
assert registry["mode"] == "fail_closed"
assert registry["classification"]["semantic_cache_key"]["crossRunReusable"] is True
assert registry["classification"]["content_fingerprint"]["crossRunReusable"] is False
assert registry["classification"]["execution_identity"]["crossRunReusable"] is False
assert registry["classification"]["immutable_artifact_reference"]["crossRunReusable"] is False
assert "strict_runtime_hash_definitions_unchanged" in registry["invariants"]
assert "semantic_cache_hit_never_reuses_old_current_artifact_ref" in registry["invariants"]
assert "semantic_cache_hit_must_preserve_current_data_version_lineage" in registry["invariants"]
print("routingVerified=true")
print("layers=" + ",".join(layer["level"] for layer in registry["layers"]))
PY

echo
echo "============================================================"
echo " PASS: unified registry -> hash lineage -> precache -> routing"
echo " Cache miss computes once and stores immutable business body."
echo " Cache hit rebinds current strict identity before normal admission."
echo " H1/H2/H3 are warmed from active report import order when present."
echo "============================================================"
