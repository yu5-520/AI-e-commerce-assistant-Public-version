#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${AI_COMPETITION_DEPLOY_V2_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SOURCE="$SOURCE_ROOT/config/deployment/competition_authorized_deploy_v2_controller.sh"
TARGET="/usr/local/sbin/ai-competition-authorized-deploy-v2"
STATE_DIR="/etc/ai-ecommerce-assistant"
HASH_FILE="$STATE_DIR/competition-authorized-deploy-v2.sha256"
SUDOERS_FILE="/etc/sudoers.d/ai-competition-authorized-deploy-v2"
STATE_ROOT="/var/lib/ai-ecommerce-authorized-deploy-v2"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run this installer as root"
[ -f "$SOURCE" ] && [ ! -L "$SOURCE" ] || fail "Controller source is missing or unsafe: $SOURCE"
command -v visudo >/dev/null 2>&1 || fail "visudo is required"
command -v sudo >/dev/null 2>&1 || fail "sudo is required"

detect_runner_user() {
  local explicit="${AI_ACTIONS_RUNNER_USER:-${1:-}}"
  local unit user pid
  if [ -n "$explicit" ]; then
    printf '%s\n' "$explicit"
    return 0
  fi

  while read -r unit; do
    [ -n "$unit" ] || continue
    case "$unit" in
      actions.runner.*AI-e-commerce-assistant-Public-version*.service)
        user="$(systemctl show "$unit" --property=User --value 2>/dev/null || true)"
        if [ -n "$user" ] && [ "$user" != "root" ]; then
          printf '%s\n' "$user"
          return 0
        fi
        pid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null || true)"
        if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
          user="$(ps -o user= -p "$pid" 2>/dev/null | awk '{$1=$1;print}' || true)"
          if [ -n "$user" ] && [ "$user" != "root" ]; then
            printf '%s\n' "$user"
            return 0
          fi
        fi
        ;;
    esac
  done < <(
    {
      systemctl list-units --type=service --all --no-legend --no-pager 2>/dev/null || true
      systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null || true
    } | awk '{print $1}' | sort -u
  )

  ps -eo user=,args= 2>/dev/null \
    | awk '$0 ~ /Runner\.(Listener|Worker)/ && $0 ~ /actions-runner-public/ {print $1; exit}'
}

RUNNER_USER="$(detect_runner_user "${1:-}")" || true
[ -n "$RUNNER_USER" ] || fail "Could not detect the Public-version Actions runner user; rerun with AI_ACTIONS_RUNNER_USER=<user>"
[[ "$RUNNER_USER" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Unsafe runner user name: $RUNNER_USER"
id "$RUNNER_USER" >/dev/null 2>&1 || fail "Runner user does not exist: $RUNNER_USER"
[ "$(id -u "$RUNNER_USER")" -ne 0 ] || fail "Refusing to grant the controller rule to root"

install -d -o root -g root -m 0755 "$(dirname "$TARGET")" "$STATE_DIR" "$STATE_ROOT" "$STATE_ROOT/receipts"
install -d -o root -g root -m 0700 "$STATE_ROOT/work"

TEMP_TARGET="$(mktemp "$(dirname "$TARGET")/.ai-competition-authorized-deploy-v2.XXXXXXXX")"
TEMP_HASH="$(mktemp "$STATE_DIR/.competition-authorized-deploy-v2.sha256.XXXXXXXX")"
TEMP_SUDOERS="$(mktemp "$STATE_DIR/.competition-authorized-deploy-v2.sudoers.XXXXXXXX")"
cleanup() {
  rm -f "$TEMP_TARGET" "$TEMP_HASH" "$TEMP_SUDOERS"
}
trap cleanup EXIT INT TERM

install -o root -g root -m 0755 "$SOURCE" "$TEMP_TARGET"
CONTROLLER_HASH="$(sha256sum "$TEMP_TARGET" | awk '{print $1}')"
printf '%s\n' "$CONTROLLER_HASH" > "$TEMP_HASH"
chown root:root "$TEMP_HASH"
chmod 0644 "$TEMP_HASH"

# Empty quotes after the command constrain this rule to zero command-line
# arguments. The runner is NOT granted generic bash/env/install/systemctl sudo.
printf '%s ALL=(root) NOPASSWD: %s ""\n' "$RUNNER_USER" "$TARGET" > "$TEMP_SUDOERS"
chown root:root "$TEMP_SUDOERS"
chmod 0440 "$TEMP_SUDOERS"
visudo -cf "$TEMP_SUDOERS" >/dev/null

mv -f "$TEMP_TARGET" "$TARGET"
mv -f "$TEMP_HASH" "$HASH_FILE"
mv -f "$TEMP_SUDOERS" "$SUDOERS_FILE"
trap - EXIT INT TERM

chown root:root "$TARGET" "$HASH_FILE" "$SUDOERS_FILE"
chmod 0755 "$TARGET"
chmod 0644 "$HASH_FILE"
chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" >/dev/null
[ "$(sha256sum "$TARGET" | awk '{print $1}')" = "$(tr -d '[:space:]' < "$HASH_FILE")" ] \
  || fail "Installed controller hash verification failed"

printf 'Installed immutable competition deploy controller: %s\n' "$TARGET"
printf 'Controller SHA256: %s\n' "$CONTROLLER_HASH"
printf 'Actions runner user: %s\n' "$RUNNER_USER"
printf 'Sudoers rule: %s (controller only, zero args)\n' "$SUDOERS_FILE"
printf 'Bootstrap complete. Re-run Competition Authorized Candidate Deploy V2.\n'