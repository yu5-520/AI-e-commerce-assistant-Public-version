#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${AI_DEPLOY_BOOTSTRAP_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LIBEXEC_DIR="${AI_DEPLOY_BOOTSTRAP_LIBEXEC_DIR:-/usr/local/libexec/ai-ecommerce}"
BOOTSTRAP_PATH="$LIBEXEC_DIR/deploy-bootstrap"
TRANSPORT_WRAPPER_PATH="$LIBEXEC_DIR/deploy-github-artifact"
TRANSPORT_CORE_PATH="$LIBEXEC_DIR/deploy-github-artifact-core-v22516.sh"
WRAPPER_PATH="${AI_DEPLOY_WRAPPER_PATH:-/usr/local/sbin/deploy-ai-release}"
DEPLOYMENT_ENV="${AI_DEPLOYMENT_ENV_FILE:-/etc/ai-ecommerce-assistant/deployment.env}"
LEGACY_ENV="${AI_GITHUB_ARTIFACT_ENV_FILE:-/etc/ai-ecommerce-assistant/github-artifact.env}"

fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run install_deploy_bootstrap.sh as root"
[ -f "$SOURCE_ROOT/scripts/deploy_github_artifact.sh" ] || fail "deploy_github_artifact.sh missing from sealed release"
[ -f "$SOURCE_ROOT/src/deployment/deploy_github_artifact_core_v22516.sh" ] || fail "deploy_github_artifact_core_v22516.sh missing from sealed release"
[ -f "$SOURCE_ROOT/config/deployment/deployment_preflight.py" ] || fail "deployment_preflight.py missing from sealed release"

mkdir -p "$LIBEXEC_DIR" "$(dirname "$WRAPPER_PATH")" "$(dirname "$DEPLOYMENT_ENV")"

TEMP_WRAPPER="$(mktemp "$LIBEXEC_DIR/.deploy-github-artifact.XXXXXX")"
TEMP_CORE="$(mktemp "$LIBEXEC_DIR/.deploy-github-artifact-core.XXXXXX")"
TEMP_BOOTSTRAP="$(mktemp "$LIBEXEC_DIR/.deploy-bootstrap.XXXXXX")"
TEMP_PREFLIGHT="$(mktemp "$LIBEXEC_DIR/.deployment-preflight.XXXXXX")"
cleanup() {
  rm -f "$TEMP_WRAPPER" "$TEMP_CORE" "$TEMP_BOOTSTRAP" "$TEMP_PREFLIGHT"
}
trap cleanup EXIT INT TERM

install -m 700 "$SOURCE_ROOT/scripts/deploy_github_artifact.sh" "$TEMP_WRAPPER"
install -m 700 "$SOURCE_ROOT/src/deployment/deploy_github_artifact_core_v22516.sh" "$TEMP_CORE"
install -m 700 "$SOURCE_ROOT/config/deployment/deployment_preflight.py" "$TEMP_PREFLIGHT"

cat > "$TEMP_BOOTSTRAP" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export AI_DEPLOY_GITHUB_ARTIFACT_CORE="\${AI_DEPLOY_GITHUB_ARTIFACT_CORE:-$TRANSPORT_CORE_PATH}"
exec "$TRANSPORT_WRAPPER_PATH" "\$@"
EOF
chmod 700 "$TEMP_BOOTSTRAP"

mv -f "$TEMP_WRAPPER" "$TRANSPORT_WRAPPER_PATH"
mv -f "$TEMP_CORE" "$TRANSPORT_CORE_PATH"
mv -f "$TEMP_BOOTSTRAP" "$BOOTSTRAP_PATH"
mv -f "$TEMP_PREFLIGHT" "$LIBEXEC_DIR/deployment-preflight"
trap - EXIT INT TERM

cat > "$WRAPPER_PATH" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail

DEPLOYMENT_ENV="${AI_DEPLOYMENT_ENV_FILE:-/etc/ai-ecommerce-assistant/deployment.env}"
LEGACY_ENV="${AI_GITHUB_ARTIFACT_ENV_FILE:-/etc/ai-ecommerce-assistant/github-artifact.env}"
BOOTSTRAP="${AI_DEPLOY_BOOTSTRAP_PATH:-/usr/local/libexec/ai-ecommerce/deploy-bootstrap}"
PREFLIGHT="${AI_DEPLOY_PREFLIGHT_PATH:-/usr/local/libexec/ai-ecommerce/deployment-preflight}"
ROOT="${AI_ECOMMERCE_ROOT:-/opt/ai-ecommerce-assistant}"

load_env() {
  local file="$1"
  [ -r "$file" ] || return 0
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a
}

load_env "$LEGACY_ENV"
load_env "$DEPLOYMENT_ENV"

case "${1:-}" in
  status)
    printf 'bootstrap=%s\n' "$BOOTSTRAP"
    printf 'preflight=%s\n' "$PREFLIGHT"
    printf 'root=%s\n' "$ROOT"
    printf 'artifactCore=%s\n' "${AI_DEPLOY_GITHUB_ARTIFACT_CORE:-/usr/local/libexec/ai-ecommerce/deploy-github-artifact-core-v22516.sh}"
    printf 'current=%s\n' "$(readlink -f "$ROOT/current" 2>/dev/null || true)"
    if [ -f "$ROOT/current/release/release-manifest.json" ]; then
      "${AI_RELEASE_PYTHON:-python3}" - "$ROOT/current/release/release-manifest.json" <<'PY'
import json,sys
with open(sys.argv[1], 'r') as handle:
    payload=json.load(handle)
print(json.dumps({
    'sourceCommit': payload.get('sourceCommit'),
    'productVersion': payload.get('productVersion'),
    'releaseHash': payload.get('releaseHash'),
}, ensure_ascii=False, indent=2))
PY
    fi
    exit 0
    ;;
  preflight)
    [ -n "${AI_RELEASE_PYTHON:-}" ] || { echo "ERROR: AI_RELEASE_PYTHON is not configured" >&2; exit 1; }
    [ -x "$AI_RELEASE_PYTHON" ] || { echo "ERROR: AI_RELEASE_PYTHON is not executable: $AI_RELEASE_PYTHON" >&2; exit 1; }
    [ -x "$PREFLIGHT" ] || { echo "ERROR: immutable deployment preflight is missing: $PREFLIGHT" >&2; exit 1; }
    exec "$AI_RELEASE_PYTHON" "$PREFLIGHT" \
      --root "$ROOT" \
      --backup-keep-count "${AI_ECOMMERCE_BACKUP_KEEP_COUNT:-1}" \
      --min-free-bytes "${AI_DEPLOYMENT_MIN_FREE_BYTES:-536870912}"
    ;;
  '') ;;
  [0-9a-fA-F][0-9a-fA-F]*)
    [ "${#1}" -eq 40 ] || { echo "ERROR: commit must be a 40-character SHA" >&2; exit 1; }
    export AI_RELEASE_SOURCE_COMMIT="$1"
    shift
    ;;
  *)
    echo "Usage: deploy-ai-release [40-char-commit|status|preflight]" >&2
    exit 2
    ;;
esac

: "${AI_GITHUB_TOKEN:=${GITHUB_TOKEN:-}}"
[ -n "${AI_GITHUB_TOKEN:-}" ] || { echo "ERROR: AI_GITHUB_TOKEN is not configured" >&2; exit 1; }
[ -n "${AI_RELEASE_PYTHON:-}" ] || { echo "ERROR: AI_RELEASE_PYTHON is not configured" >&2; exit 1; }
[ -x "$AI_RELEASE_PYTHON" ] || { echo "ERROR: AI_RELEASE_PYTHON is not executable: $AI_RELEASE_PYTHON" >&2; exit 1; }
[ -x "$BOOTSTRAP" ] || { echo "ERROR: immutable deploy bootstrap is missing: $BOOTSTRAP" >&2; exit 1; }

export AI_GITHUB_TOKEN AI_RELEASE_PYTHON
exec "$BOOTSTRAP" "$@"
WRAPPER

chmod 700 "$WRAPPER_PATH"

if [ ! -f "$DEPLOYMENT_ENV" ]; then
  umask 077
  cat > "$DEPLOYMENT_ENV" <<EOF
AI_GITHUB_REPOSITORY=yu5-520/AI-e-commerce-assistant-Public-version
AI_RELEASE_BRANCH=main
AI_ECOMMERCE_ROOT=/opt/ai-ecommerce-assistant
AI_RELEASE_PYTHON=/opt/ai-runtime/python/current/bin/python3.11
AI_RELEASE_KEEP_COUNT=2
AI_ECOMMERCE_BACKUP_KEEP_COUNT=1
AI_DEPLOYMENT_MIN_FREE_BYTES=536870912
EOF
  chmod 600 "$DEPLOYMENT_ENV"
fi

printf 'Installed deploy bootstrap: %s\n' "$BOOTSTRAP_PATH"
printf 'Installed immutable artifact wrapper: %s\n' "$TRANSPORT_WRAPPER_PATH"
printf 'Installed immutable artifact core: %s\n' "$TRANSPORT_CORE_PATH"
printf 'Installed deployment preflight: %s\n' "$LIBEXEC_DIR/deployment-preflight"
printf 'Installed deploy wrapper: %s\n' "$WRAPPER_PATH"
printf 'Deployment contract: %s\n' "$DEPLOYMENT_ENV"
