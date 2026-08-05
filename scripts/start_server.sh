#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-3000}"
APP_WORKERS="${APP_WORKERS:-1}"
APP_RELOAD="${APP_RELOAD:-false}"
INSTALL_DEPS_ON_START="${INSTALL_DEPS_ON_START:-auto}"
AI_RELEASE_REQUIRED="${AI_RELEASE_REQUIRED:-0}"
AI_RELEASE_VERIFIER_PATH="${AI_RELEASE_VERIFIER_PATH:-/usr/local/sbin/ai-release-verifier}"
MANIFEST="$ROOT_DIR/release/release-manifest.json"
LOCK_FILE="$ROOT_DIR/requirements.lock"
LOCK_HASH_FILE="$ROOT_DIR/.venv/.requirements-lock.sha256"
EXPECTED_RUNTIME_PYTHON="${AI_RELEASE_EXPECTED_PYTHON_VERSION:-}"
EXPECTED_PIP_FREEZE_HASH="${AI_RELEASE_EXPECTED_PIP_FREEZE_HASH:-}"
MANIFEST_SOURCE_COMMIT=""
MANIFEST_RELEASE_HASH=""

export AI_RELEASE_ROOT="$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}"

candidate_executable() {
  local candidate="$1"
  if [[ "$candidate" == */* ]]; then
    [ -x "$candidate" ] || return 1
    printf '%s\n' "$candidate"
    return 0
  fi
  command -v "$candidate" 2>/dev/null
}

select_python_311() {
  local role="$1"
  shift
  local candidate executable version
  for candidate in "$@"; do
    [ -n "$candidate" ] || continue
    executable="$(candidate_executable "$candidate")" || continue
    version="$("$executable" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
    [[ "$version" == 3.11.* ]] || continue
    if [ -n "$EXPECTED_RUNTIME_PYTHON" ] && [ "$version" != "$EXPECTED_RUNTIME_PYTHON" ]; then
      continue
    fi
    printf '%s\n' "$executable"
    return 0
  done
  printf 'No valid Python 3.11 interpreter found for %s\n' "$role" >&2
  return 1
}

select_bootstrap_python() {
  select_python_311 \
    bootstrap \
    "${AI_BOOTSTRAP_PYTHON:-}" \
    "${AI_RELEASE_PYTHON:-}" \
    "$ROOT_DIR/.venv/bin/python" \
    python3.11 \
    python3
}

select_base_python() {
  select_python_311 \
    base-runtime \
    "${AI_RELEASE_PYTHON:-}" \
    "${AI_BOOTSTRAP_PYTHON:-}" \
    python3.11 \
    python3
}

release_required=false
case "${AI_RELEASE_REQUIRED,,}" in
  1|true|yes|on) release_required=true ;;
esac

BOOTSTRAP_PYTHON="$(select_bootstrap_python)" || {
  echo "Required bootstrap Python 3.11 runtime is unavailable" >&2
  exit 1
}
export AI_BOOTSTRAP_PYTHON="$BOOTSTRAP_PYTHON"

if [ "$release_required" = true ] || [ -f "$MANIFEST" ]; then
  [ -x "$AI_RELEASE_VERIFIER_PATH" ] || {
    echo "Pinned release verifier missing: $AI_RELEASE_VERIFIER_PATH" >&2
    exit 1
  }
  [ -f "$MANIFEST" ] || {
    echo "Verified release required but manifest is missing: $MANIFEST" >&2
    exit 1
  }
  VERIFICATION_FILE="${TMPDIR:-/tmp}/ai-release-verification-$$.json"
  "$BOOTSTRAP_PYTHON" "$AI_RELEASE_VERIFIER_PATH" --root "$ROOT_DIR" --manifest "$MANIFEST" >"$VERIFICATION_FILE"
  cat "$VERIFICATION_FILE"
  MANIFEST_RUNTIME_PYTHON="$("$BOOTSTRAP_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["pythonVersion"])' "$VERIFICATION_FILE")"
  MANIFEST_PIP_FREEZE_HASH="$("$BOOTSTRAP_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["pipFreezeHash"])' "$VERIFICATION_FILE")"
  MANIFEST_SOURCE_COMMIT="$("$BOOTSTRAP_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["sourceCommit"])' "$VERIFICATION_FILE")"
  MANIFEST_RELEASE_HASH="$("$BOOTSTRAP_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["releaseHash"])' "$VERIFICATION_FILE")"
  rm -f "$VERIFICATION_FILE"
  if [ -n "$EXPECTED_RUNTIME_PYTHON" ] && [ "$EXPECTED_RUNTIME_PYTHON" != "$MANIFEST_RUNTIME_PYTHON" ]; then
    echo "Systemd Python identity differs from Manifest" >&2
    exit 1
  fi
  if [ -n "$EXPECTED_PIP_FREEZE_HASH" ] && [ "$EXPECTED_PIP_FREEZE_HASH" != "$MANIFEST_PIP_FREEZE_HASH" ]; then
    echo "Systemd dependency identity differs from Manifest" >&2
    exit 1
  fi
  EXPECTED_RUNTIME_PYTHON="$MANIFEST_RUNTIME_PYTHON"
  EXPECTED_PIP_FREEZE_HASH="$MANIFEST_PIP_FREEZE_HASH"
fi

[ -f "$LOCK_FILE" ] || {
  echo "Exact dependency lock is missing: $LOCK_FILE" >&2
  exit 1
}

if [ ! -x ".venv/bin/python" ]; then
  BASE_PYTHON="$(select_base_python)" || {
    echo "Required Python 3.11 patch runtime is unavailable" >&2
    exit 1
  }
  rm -rf .venv
  "$BASE_PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
ACTUAL_RUNTIME_PYTHON="$(python -c 'import platform; print(platform.python_version())')"
[[ "$ACTUAL_RUNTIME_PYTHON" == 3.11.* ]] || {
  echo "Application runtime must use Python 3.11" >&2
  exit 1
}
if [ -n "$EXPECTED_RUNTIME_PYTHON" ] && [ "$ACTUAL_RUNTIME_PYTHON" != "$EXPECTED_RUNTIME_PYTHON" ]; then
  echo "Runtime Python mismatch: expected $EXPECTED_RUNTIME_PYTHON, got $ACTUAL_RUNTIME_PYTHON" >&2
  exit 1
fi

CURRENT_LOCK_HASH="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
STORED_LOCK_HASH="$(cat "$LOCK_HASH_FILE" 2>/dev/null || true)"
NEED_INSTALL=false

case "$INSTALL_DEPS_ON_START" in
  always)
    NEED_INSTALL=true
    ;;
  never)
    NEED_INSTALL=false
    ;;
  auto)
    if [ "$CURRENT_LOCK_HASH" != "$STORED_LOCK_HASH" ]; then
      NEED_INSTALL=true
    elif ! python "$ROOT_DIR/scripts/check_dependency_lock.py" "$LOCK_FILE" --strict >/dev/null 2>&1; then
      NEED_INSTALL=true
    fi
    ;;
  *)
    echo "Invalid INSTALL_DEPS_ON_START=$INSTALL_DEPS_ON_START; use auto, always or never" >&2
    exit 1
    ;;
esac

if [ "$NEED_INSTALL" = true ]; then
  python -m pip install --disable-pip-version-check -r "$LOCK_FILE"
fi
ENVIRONMENT_JSON="$(python "$ROOT_DIR/scripts/check_dependency_lock.py" "$LOCK_FILE" --strict)" || {
  printf '%s\n' "$ENVIRONMENT_JSON" >&2
  echo "Runtime dependency closure verification failed" >&2
  exit 1
}
printf '%s\n' "$ENVIRONMENT_JSON"
ACTUAL_PIP_FREEZE_HASH="$(printf '%s' "$ENVIRONMENT_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["pipFreezeHash"])')"
if [ -n "$EXPECTED_PIP_FREEZE_HASH" ] && [ "$ACTUAL_PIP_FREEZE_HASH" != "$EXPECTED_PIP_FREEZE_HASH" ]; then
  echo "Runtime dependency environment mismatch" >&2
  exit 1
fi
printf '%s\n' "$CURRENT_LOCK_HASH" > "$LOCK_HASH_FILE"

mkdir -p outputs data logs

if [ "$release_required" = true ]; then
  [ -n "$MANIFEST_SOURCE_COMMIT" ] || {
    echo "V23 RC1 startup gate requires sourceCommit from verified Manifest" >&2
    exit 1
  }
  [ -n "$MANIFEST_RELEASE_HASH" ] || {
    echo "V23 RC1 startup gate requires releaseHash from verified Manifest" >&2
    exit 1
  }
  [ -f "$ROOT_DIR/src/services/registry_runtime_receipt_v23_service.py" ] || {
    echo "V23 RC1 runtime receipt gate is missing from sealed release" >&2
    exit 1
  }
  [ -f "$ROOT_DIR/config/v23_registry_runtime.json" ] || {
    echo "V23 RC1 runtime registry projection is missing from sealed release" >&2
    exit 1
  }
  REGISTRY_RECEIPT_ROOT="${AI_REGISTRY_RECEIPT_ROOT:-$ROOT_DIR/outputs/registry-receipts}"
  mkdir -p "$REGISTRY_RECEIPT_ROOT"
  GRAY_RECEIPT="${AI_REGISTRY_GRAY_RECEIPT:-$REGISTRY_RECEIPT_ROOT/gray-${MANIFEST_SOURCE_COMMIT}.json}"
  PRODUCTION_RECEIPT="${AI_REGISTRY_PRODUCTION_RECEIPT:-$REGISTRY_RECEIPT_ROOT/production-${MANIFEST_SOURCE_COMMIT}.json}"
  PRODUCTION_GATE_REPORT="${AI_REGISTRY_PRODUCTION_GATE_REPORT:-$REGISTRY_RECEIPT_ROOT/production-${MANIFEST_SOURCE_COMMIT}-gate.json}"
  [ -s "$GRAY_RECEIPT" ] || {
    echo "V23 RC1 gray receipt missing: $GRAY_RECEIPT" >&2
    exit 1
  }
  python -m src.services.registry_runtime_receipt_v23_service \
    --environment production \
    --release-commit "$MANIFEST_SOURCE_COMMIT" \
    --release-hash "$MANIFEST_RELEASE_HASH" \
    --output "$PRODUCTION_RECEIPT" \
    --report "$PRODUCTION_GATE_REPORT" \
    --gray-receipt "$GRAY_RECEIPT" \
    --allowed-output-root "$REGISTRY_RECEIPT_ROOT" \
    --source production_startup || {
      echo "V23 RC1 production startup receipt gate blocked runtime" >&2
      exit 1
    }
  [ -s "$PRODUCTION_RECEIPT" ] || {
    echo "V23 RC1 production receipt was not persisted" >&2
    exit 1
  }
  [ -s "$PRODUCTION_GATE_REPORT" ] || {
    echo "V23 RC1 production gate report was not persisted" >&2
    exit 1
  }
fi

if [ "$APP_RELOAD" = "true" ]; then
  [ "$release_required" = false ] || {
    echo "APP_RELOAD is forbidden for a sealed production release" >&2
    exit 1
  }
  exec python -m uvicorn src.api.main:app --host "$APP_HOST" --port "$APP_PORT" --reload
fi

exec python -m uvicorn src.api.main:app --host "$APP_HOST" --port "$APP_PORT" --workers "$APP_WORKERS"
