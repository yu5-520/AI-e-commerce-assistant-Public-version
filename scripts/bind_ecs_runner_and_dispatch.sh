#!/usr/bin/env bash
set -Eeuo pipefail

REPO="yu5-520/AI-e-commerce-assistant-Public-version"
RUNNER_USER="github-runner-public"
RUNNER_DIR="/opt/actions-runner-public"
RUNNER_NAME="$(hostname -s)-public-prune"
RUNNER_LABELS="public-prune,ecs,private-repo"
EXPECTED_BUNDLE_SHA256="8c46ca37519698399d0aaf93c1c970cff2472876b208f5ac561c5c3a4d6fa438"
EXPECTED_RELEASE_HASH="593b94a045c0532738ff2da0ed18ccd44179d425fac08cd8542203a644bc4d26"

select_python() {
  local candidate executable version
  for candidate in \
    "${AI_BOOTSTRAP_PYTHON:-}" \
    /opt/ai-runtime/python/current/bin/python3.11 \
    /opt/ai-runtime/python/3.11.9/bin/python3.11 \
    /opt/python/3.11.9/bin/python3.11 \
    "${AI_RELEASE_PYTHON:-}" \
    python3.11 python3
  do
    [[ -n "$candidate" ]] || continue
    if [[ "$candidate" == */* ]]; then
      [[ -x "$candidate" ]] || continue
      executable="$candidate"
    else
      executable="$(command -v "$candidate" 2>/dev/null || true)"
      [[ -n "$executable" ]] || continue
    fi
    version="$($executable -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
    if [[ "$version" == "3.11.9" ]]; then
      printf '%s\n' "$executable"
      return 0
    fi
  done
  return 1
}

PYTHON="$(select_python)" || {
  echo "ERROR：找不到部署架构固定的 Python 3.11.9。"
  exit 1
}

for cmd in curl tar git; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERROR：缺少 $cmd；不会调用系统包管理器。"
    exit 1
  }
done

TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "$TOKEN" ]] && command -v gh >/dev/null 2>&1; then
  TOKEN="$(gh auth token 2>/dev/null || true)"
fi
if [[ -z "$TOKEN" ]]; then
  CREDENTIAL="$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null || true)"
  TOKEN="$(printf '%s\n' "$CREDENTIAL" | sed -n 's/^password=//p' | head -n1)"
fi
[[ -n "$TOKEN" ]] || {
  echo "ERROR：没有读取到 ECS 已保存的 GitHub Token。"
  exit 1
}

AUTH_HEADER="Authorization: Bearer ${TOKEN}"
API_HEADER="X-GitHub-Api-Version: 2022-11-28"
ACCEPT_HEADER="Accept: application/vnd.github+json"

api_json() {
  local method="$1" url="$2" output="$3" data="${4:-}"
  local args=(--silent --show-error --location --retry 8 --retry-delay 3 --retry-all-errors
              --connect-timeout 20 --max-time 300
              -X "$method" -H "$AUTH_HEADER" -H "$API_HEADER" -H "$ACCEPT_HEADER"
              -o "$output" -w '%{http_code}')
  if [[ -n "$data" ]]; then
    args+=(-H 'Content-Type: application/json' --data-binary "$data")
  fi
  curl "${args[@]}" "$url"
}

echo "============================================================"
echo "[1/8] 校验仓库、Python 与本地精准包"
echo "============================================================"
echo "DEPLOYMENT_PYTHON=$PYTHON"

REPO_JSON="$(mktemp)"
HTTP="$(api_json GET "https://api.github.com/repos/${REPO}" "$REPO_JSON")"
[[ "$HTTP" == "200" ]] || { echo "ERROR：仓库访问失败 HTTP=$HTTP"; cat "$REPO_JSON"; exit 1; }
"$PYTHON" - "$REPO_JSON" <<'PY'
import json, sys
repo=json.load(open(sys.argv[1], encoding='utf-8'))
assert repo.get('private') is True, 'target repository must remain private'
assert (repo.get('permissions') or {}).get('admin') is True, 'admin permission required'
print('REPOSITORY_PRIVATE=PASS')
PY

BUNDLE="$(find /opt/ai-ecommerce-public-import/artifact -maxdepth 1 -type f \
  -name "release-*-${EXPECTED_RELEASE_HASH}.tar.gz" -print -quit 2>/dev/null || true)"
[[ -n "$BUNDLE" && -f "$BUNDLE" ]] || {
  echo "ERROR：找不到前面已下载的精准包。"
  find /opt/ai-ecommerce-public-import -maxdepth 3 -type f -name 'release-*.tar.gz' -print 2>/dev/null || true
  exit 1
}
ACTUAL_SHA="$($PYTHON - "$BUNDLE" <<'PY'
import hashlib, sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
    for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
print(h.hexdigest())
PY
)"
[[ "$ACTUAL_SHA" == "$EXPECTED_BUNDLE_SHA256" ]] || {
  echo "ERROR：精准包 SHA256 不一致。"
  echo "期望：$EXPECTED_BUNDLE_SHA256"
  echo "实际：$ACTUAL_SHA"
  exit 1
}
echo "BUNDLE=$BUNDLE"
echo "BUNDLE_SHA256=PASS"

echo "============================================================"
echo "[2/8] 准备隔离 Runner 用户与输入目录"
echo "============================================================"
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$RUNNER_DIR" --shell /bin/bash "$RUNNER_USER"
fi
RUNNER_GROUP="$(id -gn "$RUNNER_USER")"
install -d -m 0750 -o "$RUNNER_USER" -g "$RUNNER_GROUP" "$RUNNER_DIR" "$RUNNER_DIR/input"
install -m 0440 -o "$RUNNER_USER" -g "$RUNNER_GROUP" "$BUNDLE" "$RUNNER_DIR/input/release-bundle.tar.gz"
[[ "$($PYTHON - "$RUNNER_DIR/input/release-bundle.tar.gz" <<'PY'
import hashlib,sys
h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print(h)
PY
)" == "$EXPECTED_BUNDLE_SHA256" ]]
echo "ISOLATED_INPUT=PASS"

echo "============================================================"
echo "[3/8] 获取 Runner 最新版本与注册令牌"
echo "============================================================"
REG_JSON="$(mktemp)"
HTTP="$(api_json POST "https://api.github.com/repos/${REPO}/actions/runners/registration-token" "$REG_JSON" '{}')"
[[ "$HTTP" == "201" ]] || { echo "ERROR：申请 Runner 注册令牌失败 HTTP=$HTTP"; cat "$REG_JSON"; exit 1; }
RELEASE_JSON="$(mktemp)"
HTTP="$(api_json GET "https://api.github.com/repos/actions/runner/releases/latest" "$RELEASE_JSON")"
[[ "$HTTP" == "200" ]] || { echo "ERROR：读取 Runner 版本失败 HTTP=$HTTP"; cat "$RELEASE_JSON"; exit 1; }

STATE_JSON="$(mktemp)"
"$PYTHON" - "$REG_JSON" "$RELEASE_JSON" "$STATE_JSON" <<'PY'
import json, platform, sys
reg=json.load(open(sys.argv[1],encoding='utf-8'))
rel=json.load(open(sys.argv[2],encoding='utf-8'))
arch={'x86_64':'x64','amd64':'x64','aarch64':'arm64','arm64':'arm64'}.get(platform.machine().lower())
assert arch, f'unsupported architecture: {platform.machine()}'
version=rel['tag_name'].lstrip('v')
name=f'actions-runner-linux-{arch}-{version}.tar.gz'
asset=next((a for a in rel.get('assets',[]) if a.get('name')==name),None)
assert asset, f'runner asset not found: {name}'
json.dump({'token':reg['token'],'expires_at':reg['expires_at'],'version':version,'arch':arch,
           'name':name,'url':asset['browser_download_url'],'digest':asset.get('digest')},open(sys.argv[3],'w'))
print(f"RUNNER_VERSION={version}")
print(f"RUNNER_ARCH={arch}")
print(f"TOKEN_EXPIRES={reg['expires_at']}")
PY
RUNNER_TOKEN="$($PYTHON -c 'import json,sys;print(json.load(open(sys.argv[1]))["token"])' "$STATE_JSON")"
ASSET_NAME="$($PYTHON -c 'import json,sys;print(json.load(open(sys.argv[1]))["name"])' "$STATE_JSON")"
ASSET_URL="$($PYTHON -c 'import json,sys;print(json.load(open(sys.argv[1]))["url"])' "$STATE_JSON")"
ASSET_DIGEST="$($PYTHON -c 'import json,sys;print(json.load(open(sys.argv[1])).get("digest") or "")' "$STATE_JSON")"
ARCHIVE="/tmp/$ASSET_NAME"

echo "============================================================"
echo "[4/8] 下载或复用 Actions Runner"
echo "============================================================"
if [[ ! -x "$RUNNER_DIR/config.sh" ]]; then
  rm -f "$ARCHIVE" "$ARCHIVE.part"
  curl --http1.1 --location --fail --progress-bar \
    --retry 10 --retry-delay 5 --retry-all-errors \
    --connect-timeout 20 --max-time 1800 \
    -o "$ARCHIVE.part" "$ASSET_URL"
  mv "$ARCHIVE.part" "$ARCHIVE"
  ACTUAL_RUNNER_SHA="$($PYTHON - "$ARCHIVE" <<'PY'
import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
    for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
print(h.hexdigest())
PY
)"
  if [[ -n "$ASSET_DIGEST" ]]; then
    [[ "$ACTUAL_RUNNER_SHA" == "${ASSET_DIGEST#sha256:}" ]] || { echo "ERROR：Runner SHA256 不一致"; exit 1; }
  fi
  tar -xzf "$ARCHIVE" -C "$RUNNER_DIR"
  chown -R "$RUNNER_USER:$RUNNER_GROUP" "$RUNNER_DIR"
else
  echo "复用已有 Runner 安装：$RUNNER_DIR"
fi

echo "============================================================"
echo "[5/8] 注册仓库级 Runner 并启动服务"
echo "============================================================"
if [[ -f "$RUNNER_DIR/.runner" ]]; then
  if [[ -x "$RUNNER_DIR/svc.sh" ]]; then
    (cd "$RUNNER_DIR" && ./svc.sh stop || true && ./svc.sh uninstall || true)
  fi
  su -s /bin/bash "$RUNNER_USER" -c "cd '$RUNNER_DIR' && ./config.sh remove --token '$RUNNER_TOKEN'" || true
fi
su -s /bin/bash "$RUNNER_USER" -c "cd '$RUNNER_DIR' && ./config.sh \
  --url 'https://github.com/${REPO}' \
  --token '$RUNNER_TOKEN' \
  --name '$RUNNER_NAME' \
  --labels '$RUNNER_LABELS' \
  --work '_work' \
  --unattended --replace"
(cd "$RUNNER_DIR" && ./svc.sh install "$RUNNER_USER" && ./svc.sh start && ./svc.sh status)

echo "============================================================"
echo "[6/8] 等待 Runner 上线"
echo "============================================================"
RUNNERS_JSON="$(mktemp)"
ONLINE=false
for attempt in $(seq 1 60); do
  HTTP="$(api_json GET "https://api.github.com/repos/${REPO}/actions/runners?per_page=100" "$RUNNERS_JSON")"
  if [[ "$HTTP" == "200" ]] && "$PYTHON" - "$RUNNERS_JSON" "$RUNNER_NAME" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding='utf-8')); name=sys.argv[2]
r=next((x for x in p.get('runners',[]) if x.get('name')==name),None)
print(f"RUNNER_STATUS={r.get('status') if r else 'missing'}")
raise SystemExit(0 if r and r.get('status')=='online' else 1)
PY
  then ONLINE=true; break; fi
  sleep 3
done
[[ "$ONLINE" == true ]] || { echo "ERROR：Runner 未上线"; exit 1; }

echo "============================================================"
echo "[7/8] 触发精准包迁移 Action"
echo "============================================================"
DISPATCH_BODY='{"ref":"main"}'
DISPATCH_OUT="$(mktemp)"
HTTP="$(api_json POST "https://api.github.com/repos/${REPO}/actions/workflows/import-precise-release-self-hosted.yml/dispatches" "$DISPATCH_OUT" "$DISPATCH_BODY")"
[[ "$HTTP" == "204" ]] || { echo "ERROR：工作流触发失败 HTTP=$HTTP"; cat "$DISPATCH_OUT"; exit 1; }
echo "WORKFLOW_DISPATCHED=PASS"

echo "============================================================"
echo "[8/8] 等待工作流开始并显示地址"
echo "============================================================"
RUNS_JSON="$(mktemp)"
RUN_URL=""
for attempt in $(seq 1 40); do
  HTTP="$(api_json GET "https://api.github.com/repos/${REPO}/actions/workflows/import-precise-release-self-hosted.yml/runs?event=workflow_dispatch&per_page=5" "$RUNS_JSON")"
  if [[ "$HTTP" == "200" ]]; then
    RUN_URL="$($PYTHON - "$RUNS_JSON" <<'PY'
import json,sys
runs=json.load(open(sys.argv[1],encoding='utf-8')).get('workflow_runs',[])
print(runs[0].get('html_url','') if runs else '')
PY
)"
    [[ -n "$RUN_URL" ]] && break
  fi
  sleep 3
done

echo
echo "============================================================"
echo "ECS Runner 已绑定，精准包迁移 Action 已启动"
echo "============================================================"
echo "仓库      ：https://github.com/${REPO}"
echo "Runner    ：$RUNNER_NAME"
echo "Runner标签：self-hosted, Linux, X64, $RUNNER_LABELS"
echo "精准包    ：$RUNNER_DIR/input/release-bundle.tar.gz"
echo "工作流    ：${RUN_URL:-已触发，稍后在 Actions 页面查看}"
echo "当前状态  ：IMPORT_IN_PROGRESS"
echo "公开状态  ：BLOCKED"

rm -f "$REPO_JSON" "$REG_JSON" "$RELEASE_JSON" "$STATE_JSON" "$RUNNERS_JSON" "$RUNS_JSON" "$DISPATCH_OUT" "$ARCHIVE" 2>/dev/null || true
