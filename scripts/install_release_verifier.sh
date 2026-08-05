#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT_DIR/scripts/release_verifier.py"
TARGET="${AI_RELEASE_VERIFIER_PATH:-/usr/local/sbin/ai-release-verifier}"
STATE_DIR="${AI_RELEASE_VERIFIER_STATE_DIR:-/etc/ai-ecommerce-assistant}"
HASH_FILE="$STATE_DIR/release-verifier.sha256"
ROTATE="${AI_RELEASE_VERIFIER_ROTATE:-0}"
EXPECTED_OLD_HASH="${AI_RELEASE_VERIFIER_EXPECTED_OLD_SHA256:-}"
EXPECTED_PYTHON_VERSION="${AI_RELEASE_EXPECTED_PYTHON_VERSION:-3.11.9}"
HOSTED_TOOLCACHE_ROOT="${RUNNER_TOOL_CACHE:-/opt/hostedtoolcache}"
HOSTED_PYTHON_DIR="$HOSTED_TOOLCACHE_ROOT/Python/$EXPECTED_PYTHON_VERSION/x64/bin"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
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
    "${AI_RELEASE_PYTHON:-}" \
    "/opt/ai-runtime/python/current/bin/python3.11" \
    "/opt/ai-runtime/python/3.11.9/bin/python3.11" \
    "/opt/python/3.11.9/bin/python3.11" \
    "$HOSTED_PYTHON_DIR/python" \
    "$HOSTED_PYTHON_DIR/python3.11" \
    python3.11 \
    python3
  do
    [ -n "$candidate" ] || continue
    executable="$(candidate_executable "$candidate")" || continue
    version="$("$executable" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
    [ "$version" = "$EXPECTED_PYTHON_VERSION" ] || continue
    printf '%s\n' "$executable"
    return 0
  done
  return 1
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run as root"
[ -f "$SOURCE" ] || fail "Verifier source missing: $SOURCE"
[ ! -L "$SOURCE" ] || fail "Verifier source must not be a symlink"

BOOTSTRAP_PYTHON="$(select_bootstrap_python)" || {
  fail "Pinned Python $EXPECTED_PYTHON_VERSION bootstrap is required for root-verifier installation"
}
export AI_BOOTSTRAP_PYTHON="$BOOTSTRAP_PYTHON"
export AI_RELEASE_PYTHON="${AI_RELEASE_PYTHON:-$BOOTSTRAP_PYTHON}"

SOURCE_HASH="$(sha256sum "$SOURCE" | awk '{print $1}')"
[[ "$SOURCE_HASH" =~ ^[0-9a-f]{64}$ ]] || fail "Candidate verifier SHA256 is invalid"

TARGET_EXISTS=false
HASH_EXISTS=false
[ -e "$TARGET" ] && TARGET_EXISTS=true
[ -e "$HASH_FILE" ] && HASH_EXISTS=true

if [ "$TARGET_EXISTS" != "$HASH_EXISTS" ]; then
  fail "Pinned verifier state is incomplete; target and hash record must exist together"
fi

if [ "$TARGET_EXISTS" = true ]; then
  [ -f "$TARGET" ] || fail "Pinned verifier target is not a regular file"
  [ ! -L "$TARGET" ] || fail "Pinned verifier target must not be a symlink"
  [ -f "$HASH_FILE" ] || fail "Pinned verifier hash record is not a regular file"
  [ ! -L "$HASH_FILE" ] || fail "Pinned verifier hash record must not be a symlink"

  PINNED_HASH="$(tr -d '[:space:]' < "$HASH_FILE")"
  ACTUAL_HASH="$(sha256sum "$TARGET" | awk '{print $1}')"
  [[ "$PINNED_HASH" =~ ^[0-9a-f]{64}$ ]] || fail "Pinned verifier hash record is invalid"
  [ "$ACTUAL_HASH" = "$PINNED_HASH" ] || fail "Installed verifier differs from the root-pinned SHA256"

  if [ "$SOURCE_HASH" = "$PINNED_HASH" ]; then
    chown root:root "$TARGET" "$HASH_FILE"
    chmod 0755 "$TARGET"
    chmod 0644 "$HASH_FILE"
    "$BOOTSTRAP_PYTHON" "$TARGET" --help >/dev/null
    printf 'Verifier already pinned: %s\nPinned verifier SHA256: %s\nBootstrap Python: %s\n' \
      "$TARGET" "$PINNED_HASH" "$BOOTSTRAP_PYTHON"
    exit 0
  fi

  case "${ROTATE,,}" in
    1|true|yes|on) ;;
    *)
      fail "Candidate verifier differs from the pinned verifier; ordinary release deployment cannot rotate root trust"
      ;;
  esac
  [ -n "$EXPECTED_OLD_HASH" ] || fail "Verifier rotation requires AI_RELEASE_VERIFIER_EXPECTED_OLD_SHA256"
  [ "$EXPECTED_OLD_HASH" = "$PINNED_HASH" ] || fail "Expected old verifier SHA256 does not match the root-pinned value"
fi

install -d -o root -g root -m 0755 "$(dirname "$TARGET")" "$STATE_DIR"
TEMP_TARGET="$(mktemp "$(dirname "$TARGET")/.ai-release-verifier.XXXXXX")"
TEMP_HASH="$(mktemp "$STATE_DIR/.release-verifier.sha256.XXXXXX")"
BACKUP_TARGET=""
BACKUP_HASH=""
COMMIT_STARTED=false
INSTALL_COMPLETE=false

if [ "$TARGET_EXISTS" = true ]; then
  BACKUP_TARGET="$(mktemp /tmp/ai-release-verifier-backup.XXXXXX)"
  BACKUP_HASH="$(mktemp /tmp/ai-release-verifier-hash-backup.XXXXXX)"
  cp -p "$TARGET" "$BACKUP_TARGET"
  cp -p "$HASH_FILE" "$BACKUP_HASH"
fi

finish_install() {
  local exit_code=$?
  trap - EXIT
  if [ "$exit_code" -ne 0 ] && [ "$COMMIT_STARTED" = true ]; then
    if [ "$TARGET_EXISTS" = true ]; then
      cp -p "$BACKUP_TARGET" "$TARGET" || true
      cp -p "$BACKUP_HASH" "$HASH_FILE" || true
      chown root:root "$TARGET" "$HASH_FILE" || true
      chmod 0755 "$TARGET" || true
      chmod 0644 "$HASH_FILE" || true
    else
      rm -f "$TARGET" "$HASH_FILE"
    fi
  fi
  rm -f "$TEMP_TARGET" "$TEMP_HASH" "$BACKUP_TARGET" "$BACKUP_HASH"
  if [ "$exit_code" -ne 0 ]; then
    exit "$exit_code"
  fi
}
trap finish_install EXIT

install -o root -g root -m 0755 "$SOURCE" "$TEMP_TARGET"
INSTALLED_HASH="$(sha256sum "$TEMP_TARGET" | awk '{print $1}')"
[ "$INSTALLED_HASH" = "$SOURCE_HASH" ] || fail "Verifier changed during installation"
printf '%s\n' "$SOURCE_HASH" > "$TEMP_HASH"
chown root:root "$TEMP_HASH"
chmod 0644 "$TEMP_HASH"
"$BOOTSTRAP_PYTHON" "$TEMP_TARGET" --help >/dev/null

COMMIT_STARTED=true
mv -f "$TEMP_TARGET" "$TARGET"
mv -f "$TEMP_HASH" "$HASH_FILE"
chown root:root "$TARGET" "$HASH_FILE"
chmod 0755 "$TARGET"
chmod 0644 "$HASH_FILE"
[ "$(sha256sum "$TARGET" | awk '{print $1}')" = "$SOURCE_HASH" ] || fail "Installed verifier post-commit SHA256 mismatch"
[ "$(tr -d '[:space:]' < "$HASH_FILE")" = "$SOURCE_HASH" ] || fail "Pinned verifier post-commit SHA256 mismatch"
"$BOOTSTRAP_PYTHON" "$TARGET" --help >/dev/null
INSTALL_COMPLETE=true

if [ "$TARGET_EXISTS" = true ]; then
  printf 'Rotated release verifier with explicit root authorization: %s\n' "$TARGET"
else
  printf 'Installed release verifier: %s\n' "$TARGET"
fi
printf 'Pinned verifier SHA256: %s\nBootstrap Python: %s\n' "$SOURCE_HASH" "$BOOTSTRAP_PYTHON"
