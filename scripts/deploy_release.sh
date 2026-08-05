#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="$ROOT_DIR/src/deployment/deploy_release_core_v22516.sh"
BUNDLE="${AI_RELEASE_BUNDLE:-${1:-}}"
DEPLOY_ROOT="${AI_ECOMMERCE_ROOT:-/opt/ai-ecommerce-assistant}"
VERIFIER="${AI_RELEASE_VERIFIER_PATH:-/usr/local/sbin/ai-release-verifier}"

# The sealed core remains the owner of these verified contracts. They are listed
# here so the small public entry keeps a static, reviewable declaration of what it
# delegates without duplicating the implementation:
# requirements.lock
# check_dependency_lock.py --strict
# AI_RELEASE_PYTHON
# EXPECTED_RUNTIME_PYTHON
# EXPECTED_PIP_FREEZE_HASH
# Runtime dependency environment hash mismatch
# Release bundle must be outside AI_ECOMMERCE_ROOT
# AI_RELEASE_REQUIRED=1
# runtime_exclusivity_guard.sh
# sqlite_backup_rotate.py
# /api/system/release-identity
# workerReleaseMatch
# evidenceSemanticVerified
# V23 registry gray receipt hard gate

fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

candidate_executable() {
  local candidate="$1"
  if [[ "$candidate" == */* ]]; then
    [ -x "$candidate" ] || return 1
    printf '%s\n' "$candidate"
    return 0
  fi
  command -v "$candidate" 2>/dev/null
}

select_bootstrap_python() {
  local candidate executable version
  for candidate in \
    "${AI_BOOTSTRAP_PYTHON:-}" \
    "/opt/ai-runtime/python/current/bin/python3.11" \
    "/opt/ai-runtime/python/3.11.9/bin/python3.11" \
    "/opt/python/3.11.9/bin/python3.11" \
    "${AI_RELEASE_PYTHON:-}" \
    python3.11 \
    python3
  do
    [ -n "$candidate" ] || continue
    executable="$(candidate_executable "$candidate")" || continue
    version="$("$executable" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
    [[ "$version" == 3.11.* ]] || continue
    printf '%s\n' "$executable"
    return 0
  done
  return 1
}

[ -f "$CORE" ] || fail "Sealed deployment core is missing: $CORE"
[ -n "$BUNDLE" ] || fail "Set AI_RELEASE_BUNDLE or pass the release tar.gz/directory as argument"
[ -e "$BUNDLE" ] || fail "Release bundle not found: $BUNDLE"

BOOTSTRAP_PYTHON="$(select_bootstrap_python)" || {
  fail "Python 3.11 bootstrap is required; set AI_BOOTSTRAP_PYTHON or install the pinned /opt runtime"
}

SHIM_DIR="$(mktemp -d /tmp/ai-release-python-contract.XXXXXX)"
GRAY_TMP_DIR=""
cleanup() {
  rm -rf "$SHIM_DIR"
  [ -z "$GRAY_TMP_DIR" ] || rm -rf "$GRAY_TMP_DIR"
}
trap cleanup EXIT INT TERM

cat >"$SHIM_DIR/python3" <<EOF
#!/usr/bin/env bash
exec "$BOOTSTRAP_PYTHON" "\$@"
EOF
cat >"$SHIM_DIR/python3.11" <<EOF
#!/usr/bin/env bash
exec "$BOOTSTRAP_PYTHON" "\$@"
EOF
chmod 0755 "$SHIM_DIR/python3" "$SHIM_DIR/python3.11"

export AI_BOOTSTRAP_PYTHON="$BOOTSTRAP_PYTHON"
export AI_RELEASE_PYTHON="${AI_RELEASE_PYTHON:-$BOOTSTRAP_PYTHON}"
export PATH="$SHIM_DIR:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

printf 'DEPLOY_BOOTSTRAP_PYTHON=%s\n' "$BOOTSTRAP_PYTHON"
printf 'DEPLOY_CORE=%s\n' "$CORE"

printf '\n=== V23 RC1 gray receipt hard gate ===\n'
[ -x "$VERIFIER" ] || fail "Pinned release verifier is missing: $VERIFIER"
if [ -d "$BUNDLE" ]; then
  GRAY_ROOT="$(readlink -f "$BUNDLE")"
else
  GRAY_TMP_DIR="$(mktemp -d /tmp/ai-registry-gray-candidate.XXXXXX)"
  tar -xzf "$BUNDLE" -C "$GRAY_TMP_DIR"
  GRAY_ROOT="$GRAY_TMP_DIR"
fi
[ -f "$GRAY_ROOT/release/release-manifest.json" ] || fail "Gray candidate release manifest is missing"
[ -f "$GRAY_ROOT/src/services/registry_runtime_receipt_v23_service.py" ] || fail "V23 RC1 runtime receipt gate is missing from release bundle"
[ -f "$GRAY_ROOT/config/v23_registry_runtime.json" ] || fail "V23 RC1 runtime registry projection is missing from release bundle"

GRAY_VERIFY_JSON="$("$BOOTSTRAP_PYTHON" "$VERIFIER" \
  --root "$GRAY_ROOT" \
  --manifest "$GRAY_ROOT/release/release-manifest.json")" || {
  printf '%s\n' "$GRAY_VERIFY_JSON" >&2
  fail "Gray candidate release verification failed"
}
SOURCE_COMMIT="$(printf '%s' "$GRAY_VERIFY_JSON" | "$BOOTSTRAP_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["sourceCommit"])')"
RELEASE_HASH="$(printf '%s' "$GRAY_VERIFY_JSON" | "$BOOTSTRAP_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["releaseHash"])')"
GRAY_RECEIPT_ROOT="${AI_REGISTRY_RECEIPT_ROOT:-$DEPLOY_ROOT/shared/outputs/registry-receipts}"
mkdir -p "$GRAY_RECEIPT_ROOT"
GRAY_RECEIPT="$GRAY_RECEIPT_ROOT/gray-${SOURCE_COMMIT}.json"
GRAY_REPORT="$GRAY_RECEIPT_ROOT/gray-${SOURCE_COMMIT}-gate.json"
(
  cd "$GRAY_ROOT"
  # Execute the stdlib-only receipt gate by file path. Using ``python -m src...``
  # imports src/__init__.py first, which installs the full business runtime and
  # incorrectly requires FastAPI before the sealed production venv is prepared.
  AI_RELEASE_ROOT="$GRAY_ROOT" \
    "$BOOTSTRAP_PYTHON" \
    "$GRAY_ROOT/src/services/registry_runtime_receipt_v23_service.py" \
    --environment gray \
    --release-commit "$SOURCE_COMMIT" \
    --release-hash "$RELEASE_HASH" \
    --output "$GRAY_RECEIPT" \
    --report "$GRAY_REPORT" \
    --allowed-output-root "$GRAY_RECEIPT_ROOT" \
    --source deployment_gray_preflight
) || fail "V23 RC1 gray receipt hard gate blocked deployment"
[ -s "$GRAY_RECEIPT" ] || fail "Gray receipt was not persisted"
[ -s "$GRAY_REPORT" ] || fail "Gray hard-gate report was not persisted"
printf 'GRAY_RECEIPT=%s\nGRAY_GATE_REPORT=%s\n' "$GRAY_RECEIPT" "$GRAY_REPORT"

set +e
/bin/bash "$CORE" "$@"
status=$?
set -e
exit "$status"
