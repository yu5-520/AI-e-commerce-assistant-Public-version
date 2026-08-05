#!/usr/bin/env bash
set -Eeuo pipefail

REPO="yu5-520/AI-e-commerce-assistant-Public-version"
RUNNER_USER="github-runner-public"
RUNNER_DIR="/opt/actions-runner-public"
RUNNER_NAME="$(hostname -s)-public-prune"
RUNNER_LABELS="public-prune,ecs,private-repo"
BUNDLE_HASH="593b94a045c0532738ff2da0ed18ccd44179d425fac08cd8542203a644bc4d26"
EXPECTED_SHA="8c46ca37519698399d0aaf93c1c970cff2472876b208f5ac561c5c3a4d6fa438"

PY="/opt/ai-runtime/python/current/bin/python3.11"
test -x "$PY"
test "$($PY -c 'import platform; print(platform.python_version())')" = "3.11.9"

TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "$TOKEN" ]] && command -v gh >/dev/null 2>&1; then
  TOKEN="$(gh auth token 2>/dev/null || true)"
fi
if [[ -z "$TOKEN" ]]; then
  CREDENTIAL="$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null || true)"
  TOKEN="$(printf '%s\n' "$CREDENTIAL" | sed -n 's/^password=//p' | head -n1)"
fi
[[ -n "$TOKEN" ]] || { echo 'ERROR: GitHub Token not found'; exit 1; }
export TOKEN REPO

BUNDLE="$(find /opt/ai-ecommerce-public-import -type f -name "release-*-${BUNDLE_HASH}.tar.gz" -print -quit 2>/dev/null || true)"
[[ -f "$BUNDLE" ]] || { echo 'ERROR: verified release bundle not found'; exit 1; }
ACTUAL_SHA="$($PY - "$BUNDLE" <<'PY'
import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
    for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
print(h.hexdigest())
PY
)"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || { echo "ERROR: bundle hash mismatch: $ACTUAL_SHA"; exit 1; }

echo '[1/6] Bundle verified'
echo "BUNDLE=$BUNDLE"

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$RUNNER_DIR" --shell /bin/bash "$RUNNER_USER"
fi
RUNNER_GROUP="$(id -gn "$RUNNER_USER")"
install -d -m 0750 -o "$RUNNER_USER" -g "$RUNNER_GROUP" "$RUNNER_DIR" "$RUNNER_DIR/input"
install -m 0440 -o "$RUNNER_USER" -g "$RUNNER_GROUP" "$BUNDLE" "$RUNNER_DIR/input/release-bundle.tar.gz"

echo '[2/6] Request runner registration and package metadata'
STATE="/tmp/public-runner-state.json"
export STATE
"$PY" <<'PY'
import json, os, platform, time, urllib.error, urllib.request
repo=os.environ['REPO']; token=os.environ['TOKEN']; out=os.environ['STATE']
h={
 'Authorization':f'Bearer {token}',
 'Accept':'application/vnd.github+json',
 'X-GitHub-Api-Version':'2022-11-28',
 'User-Agent':'public-runner-bootstrap',
}
def call(method,url,data=None):
    body=json.dumps(data).encode() if data is not None else None
    for n in range(1,9):
        try:
            r=urllib.request.Request(url,data=body,method=method,headers={**h,**({'Content-Type':'application/json'} if body else {})})
            with urllib.request.urlopen(r,timeout=90) as x:
                raw=x.read(); return json.loads(raw) if raw else None
        except (urllib.error.URLError,TimeoutError) as e:
            if n==8: raise
            print(f'network retry {n}/8: {e}',flush=True); time.sleep(n*3)
reg=call('POST',f'https://api.github.com/repos/{repo}/actions/runners/registration-token',{})
rel=call('GET','https://api.github.com/repos/actions/runner/releases/latest')
arch={'x86_64':'x64','amd64':'x64','aarch64':'arm64','arm64':'arm64'}[platform.machine().lower()]
version=rel['tag_name'].lstrip('v'); name=f'actions-runner-linux-{arch}-{version}.tar.gz'
asset=next(a for a in rel['assets'] if a['name']==name)
json.dump({'token':reg['token'],'version':version,'name':name,'url':asset['browser_download_url'],'digest':asset.get('digest')},open(out,'w'))
print(f'RUNNER_VERSION={version}')
PY

RUNNER_TOKEN="$($PY -c 'import json;print(json.load(open("/tmp/public-runner-state.json"))["token"])')"
ASSET_NAME="$($PY -c 'import json;print(json.load(open("/tmp/public-runner-state.json"))["name"])')"
ASSET_URL="$($PY -c 'import json;print(json.load(open("/tmp/public-runner-state.json"))["url"])')"
ASSET_DIGEST="$($PY -c 'import json;print(json.load(open("/tmp/public-runner-state.json")).get("digest") or "")')"
ARCHIVE="/tmp/$ASSET_NAME"
export ASSET_URL ARCHIVE

if [[ ! -x "$RUNNER_DIR/config.sh" ]]; then
  echo '[3/6] Download runner package with Python (curl-independent)'
  "$PY" <<'PY'
import os,time,urllib.request
url=os.environ['ASSET_URL']; target=os.environ['ARCHIVE']; tmp=target+'.part'
for n in range(1,9):
    try:
        with urllib.request.urlopen(url,timeout=120) as r, open(tmp,'wb') as f:
            total=int(r.headers.get('Content-Length') or 0); done=0
            while True:
                b=r.read(1024*1024)
                if not b: break
                f.write(b); done+=len(b)
                print(f'runner download: {done}/{total or "unknown"}',flush=True)
        os.replace(tmp,target); break
    except Exception as e:
        if n==8: raise
        print(f'download retry {n}/8: {e}',flush=True); time.sleep(n*5)
PY
  RUNNER_SHA="$($PY - "$ARCHIVE" <<'PY'
import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
    for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
print(h.hexdigest())
PY
)"
  if [[ -n "$ASSET_DIGEST" ]]; then
    [[ "$RUNNER_SHA" == "${ASSET_DIGEST#sha256:}" ]] || { echo 'ERROR: runner package digest mismatch'; exit 1; }
  fi
  tar -xzf "$ARCHIVE" -C "$RUNNER_DIR"
  chown -R "$RUNNER_USER:$RUNNER_GROUP" "$RUNNER_DIR"
else
  echo '[3/6] Reuse existing runner installation'
fi

echo '[4/6] Register and start repository runner'
if [[ -f "$RUNNER_DIR/.runner" ]]; then
  [[ -x "$RUNNER_DIR/svc.sh" ]] && (cd "$RUNNER_DIR" && ./svc.sh stop || true && ./svc.sh uninstall || true)
  su -s /bin/bash "$RUNNER_USER" -c "cd '$RUNNER_DIR' && ./config.sh remove --token '$RUNNER_TOKEN'" || true
fi
su -s /bin/bash "$RUNNER_USER" -c "cd '$RUNNER_DIR' && ./config.sh --url 'https://github.com/$REPO' --token '$RUNNER_TOKEN' --name '$RUNNER_NAME' --labels '$RUNNER_LABELS' --work '_work' --unattended --replace"
(cd "$RUNNER_DIR" && ./svc.sh install "$RUNNER_USER" && ./svc.sh start && ./svc.sh status)

echo '[5/6] Dispatch migration workflow'
export RUNNER_NAME
"$PY" <<'PY'
import json,os,time,urllib.request
repo=os.environ['REPO']; token=os.environ['TOKEN']; name=os.environ['RUNNER_NAME']
h={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'public-runner-dispatch','Content-Type':'application/json'}
def call(method,url,data=None):
    body=json.dumps(data).encode() if data is not None else None
    for n in range(1,9):
        try:
            r=urllib.request.Request(url,data=body,method=method,headers=h)
            with urllib.request.urlopen(r,timeout=90) as x:
                raw=x.read(); return json.loads(raw) if raw else None
        except Exception as e:
            if n==8: raise
            print(f'api retry {n}/8: {e}',flush=True); time.sleep(n*3)
for n in range(60):
    p=call('GET',f'https://api.github.com/repos/{repo}/actions/runners?per_page=100')
    r=next((x for x in p.get('runners',[]) if x.get('name')==name),None)
    print(f'runner status: {r.get("status") if r else "missing"}',flush=True)
    if r and r.get('status')=='online': break
    time.sleep(3)
else: raise SystemExit('runner did not become online')
call('POST',f'https://api.github.com/repos/{repo}/actions/workflows/import-precise-release-self-hosted.yml/dispatches',{'ref':'main'})
print('WORKFLOW_DISPATCHED=PASS')
PY

echo '[6/6] Migration started'
echo "REPOSITORY=https://github.com/$REPO"
echo "RUNNER=$RUNNER_NAME"
echo 'STATE=IMPORT_IN_PROGRESS'
echo 'PUBLICATION_ALLOWED=false'
rm -f "$STATE" "$ARCHIVE" 2>/dev/null || true
