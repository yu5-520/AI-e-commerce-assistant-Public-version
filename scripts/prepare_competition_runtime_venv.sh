#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(pwd)}"
EVIDENCE_DIR="${2:-$ROOT/dist/competition-candidate}"
VENV_ROOT="${COMPETITION_VENV_ROOT:-/opt/actions-runner-public/competition-runtime-venvs}"
BASE_PYTHON="${COMPETITION_BASE_PYTHON:-/opt/python/3.11.9/bin/python3.11}"
LOCK_FILE="$ROOT/requirements.lock"
LOCK_CHECKER="$ROOT/scripts/check_dependency_lock.py"

mkdir -p "$EVIDENCE_DIR" "$VENV_ROOT"

[ -x "$BASE_PYTHON" ] || {
  echo "Pinned base Python missing: $BASE_PYTHON" >&2
  exit 1
}
[ -f "$LOCK_FILE" ] || {
  echo "Dependency lock missing: $LOCK_FILE" >&2
  exit 1
}
[ -f "$LOCK_CHECKER" ] || {
  echo "Dependency checker missing: $LOCK_CHECKER" >&2
  exit 1
}

test "$($BASE_PYTHON -c 'import platform; print(platform.python_version())')" = "3.11.9"

LOCK_HASH="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
VENV_DIR="$VENV_ROOT/$LOCK_HASH"
VENV_PYTHON="$VENV_DIR/bin/python"
LOCK_PATH="$VENV_ROOT/.${LOCK_HASH}.lock"

exec 9>"$LOCK_PATH"
if command -v flock >/dev/null 2>&1; then
  flock -w 900 9
fi

is_valid_venv() {
  [ -x "$VENV_PYTHON" ] || return 1
  timeout 30 "$VENV_PYTHON" -c \
    'import platform,fastapi,uvicorn,sqlalchemy,pydantic,openpyxl; assert platform.python_version()=="3.11.9"' \
    >/dev/null 2>&1 || return 1
  timeout 90 "$VENV_PYTHON" "$LOCK_CHECKER" "$LOCK_FILE" --strict \
    >/dev/null 2>&1 || return 1
}

if ! is_valid_venv; then
  TMP_VENV="${VENV_DIR}.tmp-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}-$$"
  rm -rf "$TMP_VENV"
  "$BASE_PYTHON" -m venv "$TMP_VENV"

  INSTALL_LOG="$EVIDENCE_DIR/dependency-install.log"
  : > "$INSTALL_LOG"
  install_from() {
    local index_url="$1"
    local trusted_host="$2"
    echo "Installing exact lock from ${index_url}" | tee -a "$INSTALL_LOG"
    timeout 600 "$TMP_VENV/bin/python" -m pip install \
      --no-input \
      --disable-pip-version-check \
      --prefer-binary \
      --timeout 30 \
      --retries 4 \
      --index-url "$index_url" \
      --trusted-host "$trusted_host" \
      -r "$LOCK_FILE" \
      2>&1 | tee -a "$INSTALL_LOG"
  }

  if ! install_from "https://mirrors.aliyun.com/pypi/simple/" "mirrors.aliyun.com"; then
    echo "Aliyun mirror failed; retrying official PyPI." | tee -a "$INSTALL_LOG"
    install_from "https://pypi.org/simple/" "pypi.org"
  fi

  timeout 120 "$TMP_VENV/bin/python" "$LOCK_CHECKER" "$LOCK_FILE" --strict \
    | tee "$EVIDENCE_DIR/dependency-verification.json"
  timeout 30 "$TMP_VENV/bin/python" -c \
    'import platform,fastapi,uvicorn,sqlalchemy,pydantic,openpyxl; assert platform.python_version()=="3.11.9"'

  OLD_VENV="${VENV_DIR}.old-$$"
  rm -rf "$OLD_VENV"
  if [ -e "$VENV_DIR" ]; then
    mv "$VENV_DIR" "$OLD_VENV"
  fi
  mv "$TMP_VENV" "$VENV_DIR"
  rm -rf "$OLD_VENV"
else
  timeout 90 "$VENV_PYTHON" "$LOCK_CHECKER" "$LOCK_FILE" --strict \
    | tee "$EVIDENCE_DIR/dependency-verification.json"
fi

"$VENV_PYTHON" - <<'PY' | tee "$EVIDENCE_DIR/application-python.json"
import json
import platform
import sys
import fastapi
import uvicorn
import sqlalchemy
import pydantic
import openpyxl

assert platform.python_version() == "3.11.9"
print(json.dumps({
    "pythonVersion": platform.python_version(),
    "executable": sys.executable,
    "fastapi": fastapi.__version__,
    "uvicorn": uvicorn.__version__,
    "sqlalchemy": sqlalchemy.__version__,
    "pydantic": pydantic.__version__,
    "openpyxl": openpyxl.__version__,
}, sort_keys=True))
PY

# Return a regular executable launcher instead of the venv's python symlink. Several
# evidence runners normalize their --runtime-python path with pathlib.Path.resolve();
# resolving the symlink would silently escape the verified venv and execute the base
# interpreter without locked application dependencies.
#
# This file deliberately has a different name from the workflow-level
# runtime-python-launcher.sh wrappers. Those workflows may wrap this entry again; using
# a distinct name prevents them from overwriting the target and recursively executing
# themselves.
VERIFIED_VENV_LAUNCHER="$EVIDENCE_DIR/verified-venv-python.sh"
printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$VENV_PYTHON" > "$VERIFIED_VENV_LAUNCHER"
chmod 0755 "$VERIFIED_VENV_LAUNCHER"

timeout 30 "$VERIFIED_VENV_LAUNCHER" -c \
  'import platform,fastapi,uvicorn,sqlalchemy,pydantic,openpyxl; assert platform.python_version()=="3.11.9"'
printf '%s\n' "$VENV_PYTHON" > "$EVIDENCE_DIR/runtime-python-venv-target.txt"
printf '%s\n' "$VERIFIED_VENV_LAUNCHER" > "$EVIDENCE_DIR/runtime-python-launcher.txt"

# The self-hosted runner executes JavaScript actions with its bundled Node runtime,
# but that binary is not necessarily exported to shell-step PATH. Resolve the same
# trusted runner-owned executable and expose only its directory to later steps.
NODE_BIN=""
for candidate in \
  /opt/actions-runner-public/externals/node24/bin/node \
  /opt/actions-runner-public/externals/node20/bin/node \
  /opt/actions-runner-public/externals/node16/bin/node \
  /usr/local/bin/node \
  /usr/bin/node; do
  if [ -x "$candidate" ]; then
    NODE_BIN="$candidate"
    break
  fi
done
if [ -z "$NODE_BIN" ]; then
  NODE_BIN="$(
    find /opt/actions-runner-public /opt /usr/local \
      -maxdepth 7 -type f -name node -perm -u+x -print 2>/dev/null \
      | head -n 1 \
      || true
  )"
fi
if [ -n "$NODE_BIN" ] && [ -x "$NODE_BIN" ]; then
  NODE_DIR="$(dirname "$NODE_BIN")"
  if [ -n "${GITHUB_PATH:-}" ]; then
    printf '%s\n' "$NODE_DIR" >> "$GITHUB_PATH"
  fi
  "$NODE_BIN" --version > "$EVIDENCE_DIR/node-version.txt"
  printf '%s\n' "$NODE_BIN" > "$EVIDENCE_DIR/node-executable.txt"
else
  echo "Runner-owned Node executable was not found; later JavaScript syntax checks may fail." >&2
fi

printf '%s\n' "$VERIFIED_VENV_LAUNCHER"
