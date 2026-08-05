#!/usr/bin/env bash
# Shared ECS runtime resolver.
# Runtime identity is discovered from the active Nginx upstream/listening PID and
# its systemd cgroup. Service names and port numbers are metadata, not authority.

set -euo pipefail

ai_runtime_log() { printf '\n=== %s ===\n' "$1"; }
ai_runtime_fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

_ai_service_exists() {
  [ "$(systemctl show "$1" --property=LoadState --value 2>/dev/null || true)" != "not-found" ]
}

_ai_service_is_active() {
  systemctl is-active --quiet "$1" 2>/dev/null
}

_ai_service_matches_root() {
  local service="$1"
  local root_dir="$2"
  local metadata
  metadata="$(systemctl show "$service" \
    --property=WorkingDirectory \
    --property=ExecStart \
    --property=Environment \
    --property=EnvironmentFiles \
    --value 2>/dev/null || true)"
  printf '%s' "$metadata" | grep -Fq "$root_dir"
}

_ai_service_main_pid() {
  systemctl show "$1" --property=MainPID --value 2>/dev/null | grep -E '^[1-9][0-9]*$' || true
}

_ai_service_from_pid() {
  local pid="$1"
  [ -r "/proc/${pid}/cgroup" ] || return 1
  awk -F/ '$NF ~ /\.service$/ {print $NF; exit}' "/proc/${pid}/cgroup"
}

_ai_port_from_pid() {
  local pid="$1"
  [ -r "/proc/${pid}/cmdline" ] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" \
    | sed -nE 's/.*--port([= ]+)([0-9]+).*/\2/p' \
    | tail -n 1
}

_ai_listener_pid_for_port() {
  local port="$1"
  ss -lntpH 2>/dev/null \
    | sed -nE "s/.*:${port}[[:space:]].*pid=([0-9]+).*/\\1/p" \
    | head -n 1
}

_ai_nginx_upstream_ports() {
  [ -d /etc/nginx ] || return 0
  grep -RhoE 'proxy_pass[[:space:]]+http://(127\.0\.0\.1|localhost):[0-9]+' \
    /etc/nginx 2>/dev/null \
    | sed -nE 's/.*:([0-9]+)$/\1/p' \
    | sort -nu
}

_ai_active_service_for_port() {
  local root_dir="$1"
  local port="$2"
  local pid service
  pid="$(_ai_listener_pid_for_port "$port")"
  [ -n "$pid" ] || return 1
  service="$(_ai_service_from_pid "$pid")"
  [ -n "$service" ] || return 1
  _ai_service_exists "$service" || return 1
  _ai_service_is_active "$service" || return 1
  _ai_service_matches_root "$service" "$root_dir" || return 1
  printf '%s\n' "$service"
}

_ai_running_services() {
  systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null \
    | awk '{print $1}'
}

resolve_ai_runtime_service() {
  local root_dir="$1"
  local explicit="${AI_ECOMMERCE_SERVICE:-}"
  local service port

  if [ -n "$explicit" ]; then
    _ai_service_exists "$explicit" || return 1
    _ai_service_matches_root "$explicit" "$root_dir" || return 1
    printf '%s\n' "$explicit"
    return 0
  fi

  # 1. The process receiving Nginx traffic is the strongest production signal.
  while read -r port; do
    [ -n "$port" ] || continue
    service="$(_ai_active_service_for_port "$root_dir" "$port" || true)"
    if [ -n "$service" ]; then
      printf '%s\n' "$service"
      return 0
    fi
  done < <(_ai_nginx_upstream_ports)

  # 2. An explicitly configured port may identify the active process.
  if [ -n "${AI_ECOMMERCE_PORT:-}" ]; then
    service="$(_ai_active_service_for_port "$root_dir" "$AI_ECOMMERCE_PORT" || true)"
    if [ -n "$service" ]; then
      printf '%s\n' "$service"
      return 0
    fi
  fi

  # 3. Scan every running service and accept only a unit bound to this repo.
  while read -r service; do
    [ -n "$service" ] || continue
    if _ai_service_matches_root "$service" "$root_dir"; then
      printf '%s\n' "$service"
      return 0
    fi
  done < <(_ai_running_services)

  # 4. Known names are fallback only. Active units always win over inactive
  # compatibility definitions such as the old ai-ecommerce.service on port 981.
  for service in ai-operating-advisor.service ai-operating-advisor ai-ecommerce.service; do
    if _ai_service_exists "$service" \
      && _ai_service_is_active "$service" \
      && _ai_service_matches_root "$service" "$root_dir"; then
      printf '%s\n' "$service"
      return 0
    fi
  done

  # 5. Last resort for a stopped server: select an installed matching unit so it
  # can be restarted. Prefer the current advisor service before compatibility.
  for service in ai-operating-advisor.service ai-operating-advisor ai-ecommerce.service; do
    if _ai_service_exists "$service" \
      && _ai_service_matches_root "$service" "$root_dir"; then
      printf '%s\n' "$service"
      return 0
    fi
  done

  return 1
}

_ai_env_value() {
  local file="$1"
  local key="$2"
  [ -f "$file" ] || return 1
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^['\''"]|['\''"]$/, "", value)
      print value
      exit
    }
  ' "$file"
}

resolve_ai_runtime_port() {
  local root_dir="$1"
  local service="$2"
  local pid value metadata port listener_service

  if [ -n "${AI_ECOMMERCE_PORT:-}" ]; then
    printf '%s\n' "$AI_ECOMMERCE_PORT"
    return 0
  fi

  # The live MainPID command is stronger than a stale unit-file ExecStart.
  pid="$(_ai_service_main_pid "$service")"
  if [ -n "$pid" ]; then
    value="$(_ai_port_from_pid "$pid" || true)"
    if [[ "$value" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$value"
      return 0
    fi
  fi

  # Confirm an Nginx upstream by listener PID/cgroup before using it.
  while read -r port; do
    [ -n "$port" ] || continue
    listener_service="$(_ai_active_service_for_port "$root_dir" "$port" || true)"
    if [ "$listener_service" = "$service" ]; then
      printf '%s\n' "$port"
      return 0
    fi
  done < <(_ai_nginx_upstream_ports)

  metadata="$(systemctl show "$service" --property=Environment --value 2>/dev/null || true)"
  value="$(printf '%s' "$metadata" | tr ' ' '\n' | sed -n 's/^APP_PORT=//p' | tail -n 1)"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi

  metadata="$(systemctl show "$service" --property=ExecStart --value 2>/dev/null || true)"
  value="$(printf '%s' "$metadata" | sed -nE 's/.*--port([= ]+)([0-9]+).*/\2/p' | tail -n 1)"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi

  value="$(_ai_env_value "$root_dir/.env" APP_PORT 2>/dev/null || true)"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi

  printf '3000\n'
}

retire_shadow_runtime_units() {
  local root_dir="$1"
  local official_service="$2"
  local candidate metadata

  for candidate in ai-ecommerce.service; do
    [ "$candidate" != "$official_service" ] || continue
    _ai_service_exists "$candidate" || continue
    _ai_service_is_active "$candidate" && continue
    _ai_service_matches_root "$candidate" "$root_dir" || continue
    metadata="$(systemctl show "$candidate" \
      --property=ExecStart \
      --property=EnvironmentFiles \
      --value 2>/dev/null || true)"
    if printf '%s' "$metadata" | grep -Eq -- '--port([= ]+)981|/root/apps/AI-e-commerce-assistant'; then
      systemctl disable "$candidate" >/dev/null 2>&1 || true
      systemctl reset-failed "$candidate" >/dev/null 2>&1 || true
      printf 'Retired inactive shadow unit: %s\n' "$candidate"
    fi
  done
}

wait_for_layered_runtime() {
  local python_bin="$1"
  local port="$2"
  local expected_api="$3"
  local expected_product="$4"
  local attempts="${5:-40}"
  local payload resolved api_version product_version

  for _ in $(seq 1 "$attempts"); do
    if payload="$(curl -fsS --max-time 3 "http://127.0.0.1:${port}/api/version" 2>/dev/null)"; then
      resolved="$(printf '%s' "$payload" | "$python_bin" -c 'import json,sys; p=json.load(sys.stdin); print((p.get("version") or "")+"|"+((p.get("runtimeVersions") or {}).get("product") or ""))')"
      api_version="${resolved%%|*}"
      product_version="${resolved#*|}"
      if [ "$api_version" = "$expected_api" ] && [ "$product_version" = "$expected_product" ]; then
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}
