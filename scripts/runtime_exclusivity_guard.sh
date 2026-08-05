#!/usr/bin/env bash
# V22.4 negative exclusivity guard: the sealed service is not healthy until every
# other repository-bound service, stray Python runtime and forbidden legacy path
# has lost execution rights.
set -euo pipefail

_runtime_unit_exists() {
  [ "$(systemctl show "$1" --property=LoadState --value 2>/dev/null || true)" != "not-found" ]
}

_runtime_unit_matches_root() {
  local unit="$1"
  local root_dir="$2"
  local metadata
  metadata="$(systemctl show "$unit" \
    --property=WorkingDirectory \
    --property=ExecStart \
    --property=Environment \
    --property=EnvironmentFiles \
    --value 2>/dev/null || true)"
  printf '%s' "$metadata" | grep -Fq "$root_dir"
}

list_repository_runtime_units() {
  local root_dir="$1"
  local unit
  {
    systemctl list-units --type=service --all --no-legend --no-pager 2>/dev/null | awk '{print $1}'
    systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null | awk '{print $1}'
    printf '%s\n' ai-operating-advisor.service ai-ecommerce.service
  } | sort -u | while read -r unit; do
    [ -n "$unit" ] || continue
    _runtime_unit_exists "$unit" || continue
    _runtime_unit_matches_root "$unit" "$root_dir" || continue
    printf '%s\n' "$unit"
  done
}

retire_all_shadow_runtime_units() {
  local root_dir="$1"
  local official_service="$2"
  local unit retired=0
  while read -r unit; do
    [ -n "$unit" ] || continue
    [ "$unit" = "$official_service" ] && continue
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      systemctl stop "$unit"
    fi
    systemctl disable "$unit" >/dev/null 2>&1 || true
    systemctl reset-failed "$unit" >/dev/null 2>&1 || true
    printf 'Retired shadow runtime unit: %s\n' "$unit"
    retired=$((retired + 1))
  done < <(list_repository_runtime_units "$root_dir")
  printf 'Shadow runtime units retired: %s\n' "$retired"
}

_repository_runtime_pids() {
  local root_dir="$1"
  local proc pid cwd cmdline
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    [ "$pid" != "$$" ] || continue
    [ "$pid" != "$PPID" ] || continue
    cwd="$(readlink -f "$proc/cwd" 2>/dev/null || true)"
    cmdline="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
    if [[ "$cwd" != "$root_dir" && "$cwd" != "$root_dir"/* ]] \
      && [[ "$cmdline" != *"$root_dir"* ]]; then
      continue
    fi
    printf '%s' "$cmdline" | grep -Eq '(^|[ /])(python[0-9.]*|uvicorn|gunicorn|start_server\.sh)([[:space:]]|$)' || continue
    printf '%s\n' "$pid"
  done
}

retire_stray_repository_runtime_processes() {
  local root_dir="$1"
  local pids pid attempt
  pids="$(_repository_runtime_pids "$root_dir" | sort -nu | tr '\n' ' ')"
  [ -n "${pids// /}" ] || {
    echo "Stray repository runtime processes retired: 0"
    return 0
  }
  for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  for attempt in $(seq 1 20); do
    sleep 0.25
    local alive=""
    for pid in $pids; do
      [ -d "/proc/$pid" ] && alive="$alive $pid"
    done
    [ -z "${alive// /}" ] && break
  done
  for pid in $pids; do
    [ -d "/proc/$pid" ] && kill -KILL "$pid" 2>/dev/null || true
  done
  printf 'Stray repository runtime processes retired: %s\n' "$pids"
}

retire_forbidden_legacy_paths() {
  local root_dir="$1"
  local manifest_path="$2"
  [ -f "$manifest_path" ] || {
    printf 'Release manifest missing for legacy cleanup: %s\n' "$manifest_path" >&2
    return 1
  }
  python3 - "$root_dir" "$manifest_path" <<'PY'
import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
removed = []
for raw_value in manifest.get("forbiddenPaths") or []:
    raw = str(raw_value or "").strip()
    relative = Path(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise SystemExit("unsafe_forbidden_legacy_path:{0}".format(raw))
    target = (root / relative).resolve(strict=False)
    if root != target and root not in target.parents:
        raise SystemExit("forbidden_legacy_path_escapes_root:{0}".format(raw))
    if target.is_symlink() or target.is_file():
        target.unlink()
        removed.append(raw)
    elif target.is_dir():
        shutil.rmtree(str(target))
        removed.append(raw)
for raw in removed:
    print("Removed forbidden legacy path: {0}".format(raw))
print("Forbidden legacy paths retired: {0}".format(len(removed)))
PY
}

retire_legacy_working_tree_after_success() {
  local root_dir="$1"
  python3 - "$root_dir" <<'PY'
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
required = {"releases", "shared", "current"}
missing = [name for name in ("releases", "shared", "current") if not (root / name).exists()]
if missing:
    raise SystemExit("release_root_not_ready_for_legacy_retirement:" + ",".join(missing))
removed = []
for child in root.iterdir():
    if child.name in required:
        continue
    if child.is_symlink() or child.is_file():
        child.unlink()
    elif child.is_dir():
        shutil.rmtree(str(child))
    elif child.exists():
        child.unlink()
    removed.append(child.name)
for name in sorted(removed):
    print("Retired legacy working-tree entry: {0}".format(name))
print("Legacy working-tree entries retired: {0}".format(len(removed)))
PY
}

assert_no_stray_repository_runtime_processes() {
  local root_dir="$1"
  local pids
  pids="$(_repository_runtime_pids "$root_dir" | sort -nu | tr '\n' ' ')"
  [ -z "${pids// /}" ] || {
    printf 'Unexpected stray repository runtime processes: %s\n' "$pids" >&2
    return 1
  }
}

assert_one_repository_runtime_unit() {
  local root_dir="$1"
  local official_service="$2"
  local count=0 unit
  while read -r unit; do
    [ -n "$unit" ] || continue
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      count=$((count + 1))
      [ "$unit" = "$official_service" ] || {
        printf 'Unexpected active runtime unit: %s\n' "$unit" >&2
        return 1
      }
    fi
  done < <(list_repository_runtime_units "$root_dir")
  [ "$count" -eq 1 ] || {
    printf 'Expected exactly one active repository runtime unit, found %s\n' "$count" >&2
    return 1
  }
}
