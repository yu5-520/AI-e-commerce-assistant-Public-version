#!/usr/bin/env bash
set -euo pipefail

TARGET_REPO="yu5-520/AI-e-commerce-assistant-Public-version"
CANDIDATE_BRANCH="migration/precise-bulk-candidate"
SOURCE_COMMIT="f5186451c80631fea550da17d481f5e8793215e5"
EXPECTED_TREE="5e7b8b6b1f54316328a3cd5e436cdc7a18007940"
EXPECTED_COUNT="605"
EXPECTED_BYTES="5966613"
EXPECTED_LOCAL_TGZ_SHA="8c46ca37519698399d0aaf93c1c970cff2472876b208f5ac561c5c3a4d6fa438"
EXPECTED_XZ_SHA="1e7916ae07a9f27733d526eacac6d4ff26620febd7078ee5b000c8289805ea50"
ADOBE_URL="https://at.adobe.com/vhJXvNHQOIE8hlWL"

LOCAL_TGZ="/opt/ai-ecommerce-public-import/artifact/release-f5186451c80631fea550da17d481f5e8793215e5-593b94a045c0532738ff2da0ed18ccd44179d425fac08cd8542203a644bc4d26.tar.gz"
RUNNER_TGZ="/opt/actions-runner-public/input/release-bundle.tar.gz"
WORK="$(mktemp -d /tmp/precise-bulk-import.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/stage"
mkdir -p "$STAGE"

if [[ -f "$LOCAL_TGZ" ]] && [[ "$(sha256sum "$LOCAL_TGZ" | awk '{print $1}')" == "$EXPECTED_LOCAL_TGZ_SHA" ]]; then
  echo "SOURCE=ecs-local-precise-bundle"
  tar -xzf "$LOCAL_TGZ" -C "$STAGE"
elif [[ -f "$RUNNER_TGZ" ]] && [[ "$(sha256sum "$RUNNER_TGZ" | awk '{print $1}')" == "$EXPECTED_LOCAL_TGZ_SHA" ]]; then
  echo "SOURCE=runner-local-precise-bundle"
  tar -xzf "$RUNNER_TGZ" -C "$STAGE"
else
  echo "SOURCE=temporary-sealed-archive"
  ARCHIVE="$WORK/precise-bundle.tar.xz"
  curl -fL --retry 5 --retry-all-errors --retry-delay 2 --connect-timeout 20 --max-time 180 \
    -H 'Accept: application/octet-stream' "$ADOBE_URL" -o "$ARCHIVE"
  echo "$EXPECTED_XZ_SHA  $ARCHIVE" | sha256sum -c -
  tar -xJf "$ARCHIVE" -C "$STAGE"
fi

FILE_COUNT="$(find "$STAGE" -type f -printf '.' | wc -c)"
TOTAL_BYTES="$(find "$STAGE" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}')"
echo "TRANSFERRED_FILE_COUNT=$FILE_COUNT"
echo "TRANSFERRED_TOTAL_BYTES=$TOTAL_BYTES"
test "$FILE_COUNT" = "$EXPECTED_COUNT"
test "$TOTAL_BYTES" = "$EXPECTED_BYTES"

test -x "$STAGE/src/deployment/deploy_github_artifact_core_v22516.sh"
test -x "$STAGE/src/deployment/deploy_release_core_v22516.sh"

PYTHON_BIN=""
for candidate in /opt/ai-runtime/python/current/bin/python3.11 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PYTHON_BIN="$(command -v "$candidate")"; break; fi
done
test -n "$PYTHON_BIN"
"$PYTHON_BIN" "$STAGE/scripts/release_verifier.py" --root "$STAGE"

cd "$STAGE"
git init -q -b precise-import
git config user.name "precise-migration"
git config user.email "precise-migration@users.noreply.github.com"
git config core.filemode true
git add -A
INDEX_COUNT="$(git ls-files | wc -l)"
ROOT_TREE="$(git write-tree)"
echo "INDEX_COUNT=$INDEX_COUNT"
echo "ROOT_TREE=$ROOT_TREE"
test "$INDEX_COUNT" = "$EXPECTED_COUNT"
test "$ROOT_TREE" = "$EXPECTED_TREE"

git commit -q -m "migration: import precise release ${SOURCE_COMMIT}"
COMMIT_SHA="$(git rev-parse HEAD)"

TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "$TOKEN" ]] && command -v gh >/dev/null 2>&1; then
  TOKEN="$(gh auth token 2>/dev/null || true)"
fi
if [[ -n "$TOKEN" ]]; then
  git remote add origin "https://x-access-token:${TOKEN}@github.com/${TARGET_REPO}.git"
else
  git remote add origin "https://github.com/${TARGET_REPO}.git"
fi

git push --force origin "HEAD:refs/heads/${CANDIDATE_BRANCH}"
REMOTE_COMMIT="$(git ls-remote origin "refs/heads/${CANDIDATE_BRANCH}" | awk '{print $1}')"
test "$REMOTE_COMMIT" = "$COMMIT_SHA"

echo "PRECISE_BULK_TRANSFER=PASS"
echo "CANDIDATE_COMMIT=$COMMIT_SHA"
echo "CANDIDATE_ROOT_TREE=$ROOT_TREE"
echo "PUBLICATION_ALLOWED=false"
