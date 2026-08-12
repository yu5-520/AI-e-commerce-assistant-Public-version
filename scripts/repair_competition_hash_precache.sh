#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "============================================================"
echo " Competition Unified Registry -> Hash Lineage -> Precache"
echo "============================================================"

if [ -n "${PYTHON_BIN:-}" ] && [ -x "${PYTHON_BIN}" ]; then
  PY="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "ERROR: existing Python runtime not found" >&2
  exit 1
fi

"$PY" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ required")
print("python=" + sys.executable)
print("version=" + sys.version.split()[0])
PY

echo
echo "[1/6] Verify existing unified Registry and Hash Lineage"
bash scripts/repair_competition_runtime_contracts.sh

echo
echo "[2/6] Compile semantic-precache runtime and verifier"
"$PY" -m py_compile \
  src/services/competition_hash_precache_registry_v1_service.py \
  src/services/competition_evidence_v215_runtime_service.py \
  scripts/verify_competition_hash_precache_registry.py \
  tests/test_competition_hash_precache_registry_v1.py

echo
echo "[3/6] Verify layered/classified unified precache registry"
"$PY" scripts/verify_competition_hash_precache_registry.py

echo
echo "[4/6] Run semantic/execution identity regression tests"
"$PY" -m unittest -q tests.test_competition_hash_precache_registry_v1

echo
echo "[5/6] Initialize cache index and warm active report prefixes when available"
"$PY" - <<'PY'
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
    for version in versions:
        result = signal_snapshot.materialize_product_signal_snapshot(
            data_version=version,
            force=False,
        )
        receipts.append(
            {
                "dataVersion": version,
                "preAgentComputeHash": result.get("preAgentComputeHash"),
                "semanticCacheHit": result.get("semanticCacheHit"),
                "preAgentCacheStatus": result.get("preAgentCacheStatus"),
                "evidenceInputHash": result.get("evidenceInputHash"),
            }
        )
print(json.dumps({"activePrefixVersions": versions, "warmReceipts": receipts, "status": hash_precache_status()}, ensure_ascii=False, sort_keys=True))
PY

echo
echo "[6/6] Assert strict/semantic routing contract"
"$PY" - <<'PY'
import json
from pathlib import Path
registry = json.loads(Path("config/competition_hash_precache_registry_v1.json").read_text(encoding="utf-8"))
assert registry["classification"]["semantic_cache_key"]["crossRunReusable"] is True
assert registry["classification"]["execution_identity"]["crossRunReusable"] is False
assert registry["classification"]["immutable_artifact_reference"]["crossRunReusable"] is False
assert "strict_runtime_hash_definitions_unchanged" in registry["invariants"]
assert "semantic_cache_hit_never_reuses_old_current_artifact_ref" in registry["invariants"]
print("routingVerified=true")
print("layers=" + ",".join(layer["level"] for layer in registry["layers"]))
PY

echo
echo "============================================================"
echo " PASS: unified registry -> hash lineage -> precache -> routing"
echo " Cache miss computes once and stores immutable business body."
echo " Cache hit rebinds current strict identity before normal admission."
echo "============================================================"
