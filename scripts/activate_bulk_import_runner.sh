#!/usr/bin/env bash
set -Eeuo pipefail

REPO="yu5-520/AI-e-commerce-assistant-Public-version"
RUNNER_USER="github-runner-public"
RUNNER_DIR="/opt/actions-runner-public"
RUNNER_NAME="$(hostname -s)-public-bulk-import"

[[ "$(id -u)" == "0" ]] || { echo "ERROR: 请用 sudo/root 执行"; exit 1; }
for cmd in curl tar git python3; do command -v "$cmd" >/dev/null || { echo "ERROR: 缺少 $cmd"; exit 1; }; done

# 已配置的隔离 Runner：只拉起服务，不重新注册。
if [[ -f "$RUNNER_DIR/.runner" && -x "$RUNNER_DIR/svc.sh" ]]; then
  cd "$RUNNER_DIR"
  ./svc.sh start || true
  ./svc.sh status
  echo "RUNNER_ACTIVATED=PASS"
  exit 0
fi

TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "$TOKEN" ]] && command -v gh >/dev/null 2>&1; then TOKEN="$(gh auth token 2>/dev/null || true)"; fi
if [[ -z "$TOKEN" ]]; then
  credential="$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null || true)"
  TOKEN="$(printf '%s\n' "$credential" | sed -n 's/^password=//p' | head -n1)"
fi
[[ -n "$TOKEN" ]] || { echo "ERROR: 未读取到 ECS 保存的 GitHub Token"; exit 1; }

api() {
  local method="$1" url="$2" out="$3" data="${4:-}"
  local args=(--silent --show-error --location --fail-with-body --retry 6 --retry-all-errors
    -X "$method" -H "Authorization: Bearer $TOKEN"
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28" -o "$out")
  [[ -z "$data" ]] || args+=(-H 'Content-Type: application/json' --data-binary "$data")
  curl "${args[@]}" "$url"
}

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$RUNNER_DIR" --shell /bin/bash "$RUNNER_USER"
fi
RUNNER_GROUP="$(id -gn "$RUNNER_USER")"
install -d -m 0750 -o "$RUNNER_USER" -g "$RUNNER_GROUP" "$RUNNER_DIR"

registration="$(mktemp)"
release="$(mktemp)"
api POST "https://api.github.com/repos/$REPO/actions/runners/registration-token" "$registration" '{}'
api GET "https://api.github.com/repos/actions/runner/releases/latest" "$release"

readarray -t state < <(python3 - "$registration" "$release" <<'PY'
import json, platform, sys
reg=json.load(open(sys.argv[1],encoding='utf-8'))
rel=json.load(open(sys.argv[2],encoding='utf-8'))
arch={'x86_64':'x64','amd64':'x64','aarch64':'arm64','arm64':'arm64'}[platform.machine().lower()]
version=rel['tag_name'].lstrip('v')
name=f'actions-runner-linux-{arch}-{version}.tar.gz'
asset=next(a for a in rel['assets'] if a['name']==name)
print(reg['token']); print(asset['browser_download_url']); print(asset.get('digest') or '')
PY
)
RUNNER_TOKEN="${state[0]}"
ASSET_URL="${state[1]}"
ASSET_DIGEST="${state[2]}"

if [[ ! -x "$RUNNER_DIR/config.sh" ]]; then
  archive="$(mktemp --suffix=.tar.gz)"
  curl --http1.1 --location --fail --progress-bar --retry 10 --retry-all-errors \
    --connect-timeout 20 --max-time 1800 -o "$archive" "$ASSET_URL"
  if [[ -n "$ASSET_DIGEST" ]]; then
    echo "${ASSET_DIGEST#sha256:}  $archive" | sha256sum -c -
  fi
  tar -xzf "$archive" -C "$RUNNER_DIR"
  chown -R "$RUNNER_USER:$RUNNER_GROUP" "$RUNNER_DIR"
  rm -f "$archive"
fi

su -s /bin/bash "$RUNNER_USER" -c "cd '$RUNNER_DIR' && ./config.sh \
  --url 'https://github.com/$REPO' --token '$RUNNER_TOKEN' \
  --name '$RUNNER_NAME' --labels 'public-prune,ecs,private-repo' \
  --work '_work' --unattended --replace"

cd "$RUNNER_DIR"
./svc.sh install "$RUNNER_USER"
./svc.sh start
./svc.sh status

echo "RUNNER_ACTIVATED=PASS"
echo "QUEUED_WORKFLOW_WILL_START_AUTOMATICALLY=YES"
rm -f "$registration" "$release"
