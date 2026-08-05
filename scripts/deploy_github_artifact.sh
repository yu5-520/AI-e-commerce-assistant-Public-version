#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="${AI_DEPLOY_GITHUB_ARTIFACT_CORE:-$ROOT_DIR/src/deployment/deploy_github_artifact_core_v22516.sh}"

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

[ -f "$CORE" ] || fail "Sealed GitHub artifact transport core is missing: $CORE"

BOOTSTRAP_PYTHON="$(select_bootstrap_python)" || {
  fail "Python 3.11 bootstrap is required; set AI_BOOTSTRAP_PYTHON or install the pinned /opt runtime"
}

if [ -z "${AI_RELEASE_SOURCE_COMMIT:-}" ] && [[ "${1:-}" =~ ^[0-9a-fA-F]{40}$ ]]; then
  export AI_RELEASE_SOURCE_COMMIT="$1"
  shift
fi

export AI_BOOTSTRAP_PYTHON="$BOOTSTRAP_PYTHON"
export AI_RELEASE_PYTHON="${AI_RELEASE_PYTHON:-$BOOTSTRAP_PYTHON}"

printf 'ARTIFACT_BOOTSTRAP_PYTHON=%s\n' "$BOOTSTRAP_PYTHON"
printf 'ARTIFACT_TRANSPORT_CORE=%s\n' "$CORE"

/bin/bash "$CORE" "$@"
