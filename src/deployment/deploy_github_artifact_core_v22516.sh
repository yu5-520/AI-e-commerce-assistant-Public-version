#!/usr/bin/env bash
set -euo pipefail

# Fetches a completed, hash-sealed GitHub Actions artifact through api.github.com
# and delegates the actual deployment to the candidate bundle's deploy_release.sh.
# Formal deployment accepts only push-to-main artifacts whose workflow run,
# Artifact metadata, sealed manifest and test attestation share one commit identity.
# It never deploys a mutable branch working tree and never runs Git transport commands.

REPOSITORY="${AI_GITHUB_REPOSITORY:-yu5-520/AI-e-commerce-assistant-Public-version}"
BRANCH="${AI_RELEASE_BRANCH:-main}"
FORMAL_BRANCH="main"
FORMAL_WORKFLOW_PATH=".github/workflows/release-hash-seal.yml"
API_BASE="${AI_GITHUB_API_BASE:-https://api.github.com}"
ROOT_DIR="${AI_ECOMMERCE_ROOT:-/opt/ai-ecommerce-assistant}"
BOOTSTRAP_PYTHON="${AI_BOOTSTRAP_PYTHON:-python3}"
RELEASE_PYTHON="${AI_RELEASE_PYTHON:-/usr/bin/python3.11}"
SOURCE_COMMIT="${AI_RELEASE_SOURCE_COMMIT:-}"
MAX_ATTEMPTS="${AI_GITHUB_MAX_ATTEMPTS:-8}"
CONNECT_TIMEOUT="${AI_GITHUB_CONNECT_TIMEOUT:-15}"
REQUEST_TIMEOUT="${AI_GITHUB_REQUEST_TIMEOUT:-180}"
TOKEN="${AI_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"

log() { printf '\n=== %s ===\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run deploy_github_artifact.sh as root"
[[ "$REPOSITORY" == */* ]] || fail "AI_GITHUB_REPOSITORY must be owner/repository"
[[ "$API_BASE" == https://* ]] || fail "AI_GITHUB_API_BASE must use HTTPS"
[ "$BRANCH" = "$FORMAL_BRANCH" ] || fail "Only main push artifacts are deployable; AI_RELEASE_BRANCH must be main"
[[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || fail "AI_GITHUB_MAX_ATTEMPTS must be a positive integer"
if [ -n "$SOURCE_COMMIT" ]; then
  [[ "$SOURCE_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || fail "AI_RELEASE_SOURCE_COMMIT must be a 40-character commit SHA"
fi
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v "$BOOTSTRAP_PYTHON" >/dev/null 2>&1 || fail "$BOOTSTRAP_PYTHON is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"

WORK_DIR="$(mktemp -d /tmp/ai-release-transport.XXXXXX)"
REF_JSON="$WORK_DIR/ref.json"
ARTIFACT_JSON="$WORK_DIR/artifacts.json"
RUN_JSON="$WORK_DIR/workflow-run.json"
ARTIFACT_ZIP="$WORK_DIR/release-artifact.zip"
ARTIFACT_DIR="$WORK_DIR/artifact"
CANDIDATE_DIR="$WORK_DIR/candidate"
DOWNLOAD_HEADERS="$WORK_DIR/download.headers"
DOWNLOAD_BODY="$WORK_DIR/download.body"
mkdir -p "$ARTIFACT_DIR" "$CANDIDATE_DIR"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

API_HEADERS=(
  -H "Accept: application/vnd.github+json"
  -H "X-GitHub-Api-Version: 2022-11-28"
  -H "User-Agent: ai-ecommerce-release-transport"
)
if [ -n "$TOKEN" ]; then
  API_HEADERS+=( -H "Authorization: Bearer $TOKEN" )
fi

api_to_file() {
  local url="$1"
  local output="$2"
  local label="$3"
  local attempt delay

  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    printf '%s attempt %s/%s\n' "$label" "$attempt" "$MAX_ATTEMPTS"
    if curl \
      --fail \
      --silent \
      --show-error \
      --connect-timeout "$CONNECT_TIMEOUT" \
      --max-time "$REQUEST_TIMEOUT" \
      "${API_HEADERS[@]}" \
      --output "$output" \
      "$url"; then
      [ -s "$output" ] || fail "$label returned an empty response"
      return 0
    fi
    rm -f "$output"
    delay=$((attempt * 4))
    printf '%s failed; retrying in %ss\n' "$label" "$delay" >&2
    sleep "$delay"
  done
  return 1
}

public_to_file() {
  local url="$1"
  local output="$2"
  local label="$3"
  local attempt delay

  [[ "$url" == https://* ]] || fail "$label redirect must use HTTPS"
  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    printf '%s attempt %s/%s\n' "$label" "$attempt" "$MAX_ATTEMPTS"
    if curl \
      --fail \
      --silent \
      --show-error \
      --location \
      --connect-timeout "$CONNECT_TIMEOUT" \
      --max-time "$REQUEST_TIMEOUT" \
      -H "User-Agent: ai-ecommerce-release-transport" \
      --output "$output" \
      "$url"; then
      [ -s "$output" ] || fail "$label returned an empty response"
      return 0
    fi
    rm -f "$output"
    delay=$((attempt * 4))
    printf '%s failed; retrying in %ss\n' "$label" "$delay" >&2
    sleep "$delay"
  done
  return 1
}

download_artifact_zip() {
  local url="$1"
  local attempt delay http_code location

  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    printf 'artifact redirect request attempt %s/%s\n' "$attempt" "$MAX_ATTEMPTS"
    rm -f "$DOWNLOAD_HEADERS" "$DOWNLOAD_BODY"
    if http_code="$(curl \
      --silent \
      --show-error \
      --connect-timeout "$CONNECT_TIMEOUT" \
      --max-time "$REQUEST_TIMEOUT" \
      --max-redirs 0 \
      "${API_HEADERS[@]}" \
      --dump-header "$DOWNLOAD_HEADERS" \
      --output "$DOWNLOAD_BODY" \
      --write-out '%{http_code}' \
      "$url")"; then
      if [ "$http_code" = "200" ] && [ -s "$DOWNLOAD_BODY" ]; then
        if "$BOOTSTRAP_PYTHON" - "$DOWNLOAD_BODY" <<'PY'
import sys
with open(sys.argv[1], "rb") as handle:
    raise SystemExit(0 if handle.read(4).startswith(b"PK") else 1)
PY
        then
          mv "$DOWNLOAD_BODY" "$ARTIFACT_ZIP"
          return 0
        fi
      fi
      if [[ "$http_code" =~ ^30[12378]$ ]]; then
        location="$(awk '
          BEGIN { IGNORECASE=1 }
          /^Location:/ {
            sub(/\r$/, "")
            sub(/^[^:]*:[[:space:]]*/, "")
            print
            exit
          }
        ' "$DOWNLOAD_HEADERS")"
        [ -n "$location" ] || fail "GitHub artifact redirect omitted Location"
        public_to_file "$location" "$ARTIFACT_ZIP" "artifact object download" && return 0
      fi
      printf 'artifact API returned HTTP %s\n' "$http_code" >&2
    fi
    delay=$((attempt * 4))
    printf 'artifact redirect request failed; retrying in %ss\n' "$delay" >&2
    sleep "$delay"
  done
  return 1
}

log "1. Resolve exact main source commit"
if [ -z "$SOURCE_COMMIT" ]; then
  REF_URL="$API_BASE/repos/$REPOSITORY/commits/$FORMAL_BRANCH"
  api_to_file "$REF_URL" "$REF_JSON" "main head request" || \
    fail "Cannot resolve current main commit through GitHub API"
  SOURCE_COMMIT="$($BOOTSTRAP_PYTHON - "$REF_JSON" <<'PY'
import json
import sys
with open(sys.argv[1], "r") as handle:
    print(json.load(handle).get("sha") or "")
PY
  )"
fi
[[ "$SOURCE_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || fail "Resolved source commit is invalid"
printf 'requestedSourceCommit=%s\nrequestedBranch=%s\n' "$SOURCE_COMMIT" "$FORMAL_BRANCH"

log "2. Resolve exact main-push sealed artifact"
ARTIFACTS_URL="$API_BASE/repos/$REPOSITORY/actions/artifacts?per_page=100"
api_to_file "$ARTIFACTS_URL" "$ARTIFACT_JSON" "artifact metadata request" || \
  fail "Cannot reach GitHub Actions artifact API after $MAX_ATTEMPTS attempts"

SELECTION="$($BOOTSTRAP_PYTHON - "$ARTIFACT_JSON" "$FORMAL_BRANCH" "$SOURCE_COMMIT" <<'PY'
import json
import sys

path, branch, requested_sha = sys.argv[1:4]
expected_name = "release-formal-" + requested_sha
with open(path, "r") as handle:
    payload = json.load(handle)

candidates = []
for artifact in payload.get("artifacts") or []:
    run = artifact.get("workflow_run") or {}
    name = str(artifact.get("name") or "")
    head_sha = str(run.get("head_sha") or "")
    run_id = str(run.get("id") or "")
    if artifact.get("expired"):
        continue
    if name != expected_name:
        continue
    if str(run.get("head_branch") or "") != branch:
        continue
    if head_sha != requested_sha:
        continue
    if not run_id:
        continue
    candidates.append(artifact)

candidates.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
if not candidates:
    raise SystemExit(3)

artifact = candidates[0]
run = artifact.get("workflow_run") or {}
values = [
    str(artifact.get("id") or ""),
    str(run.get("head_sha") or ""),
    str(artifact.get("name") or ""),
    str(artifact.get("digest") or ""),
    str(artifact.get("archive_download_url") or ""),
    str(run.get("id") or ""),
]
if not values[0] or len(values[1]) != 40 or not values[4] or not values[5]:
    raise SystemExit(4)
print("\t".join(values))
PY
)" || fail "Commit $SOURCE_COMMIT does not yet have an unexpired release-formal-$SOURCE_COMMIT Artifact on main"

IFS=$'\t' read -r ARTIFACT_ID RESOLVED_COMMIT ARTIFACT_NAME ARTIFACT_DIGEST DOWNLOAD_URL RUN_ID <<< "$SELECTION"
printf 'artifactId=%s\nartifactName=%s\nsourceCommit=%s\nworkflowRunId=%s\n' \
  "$ARTIFACT_ID" "$ARTIFACT_NAME" "$RESOLVED_COMMIT" "$RUN_ID"

log "3. Verify workflow run is a successful push to main"
RUN_URL="$API_BASE/repos/$REPOSITORY/actions/runs/$RUN_ID"
api_to_file "$RUN_URL" "$RUN_JSON" "workflow run metadata request" || \
  fail "Cannot read workflow run $RUN_ID"

RUN_IDENTITY="$($BOOTSTRAP_PYTHON - "$RUN_JSON" "$FORMAL_BRANCH" "$SOURCE_COMMIT" "$FORMAL_WORKFLOW_PATH" <<'PY'
import json
import sys

path, branch, requested_sha, expected_path = sys.argv[1:5]
with open(path, "r") as handle:
    run = json.load(handle)

checks = {
    "event": str(run.get("event") or ""),
    "status": str(run.get("status") or ""),
    "conclusion": str(run.get("conclusion") or ""),
    "head_branch": str(run.get("head_branch") or ""),
    "head_sha": str(run.get("head_sha") or ""),
    "path": str(run.get("path") or ""),
}
expected = {
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "head_branch": branch,
    "head_sha": requested_sha,
    "path": expected_path,
}
errors = [
    key + "=" + repr(checks[key]) + " expected=" + repr(value)
    for key, value in expected.items()
    if checks[key] != value
]
if errors:
    sys.stderr.write("; ".join(errors) + "\n")
    raise SystemExit(5)
print("\t".join([
    checks["event"],
    checks["status"],
    checks["conclusion"],
    checks["head_branch"],
    checks["head_sha"],
    checks["path"],
]))
PY
)" || fail "Artifact workflow run is not a successful push-to-main release seal"

IFS=$'\t' read -r RUN_EVENT RUN_STATUS RUN_CONCLUSION RUN_BRANCH RUN_COMMIT RUN_PATH <<< "$RUN_IDENTITY"
printf 'workflowEvent=%s\nworkflowStatus=%s\nworkflowConclusion=%s\nworkflowBranch=%s\nworkflowCommit=%s\nworkflowPath=%s\n' \
  "$RUN_EVENT" "$RUN_STATUS" "$RUN_CONCLUSION" "$RUN_BRANCH" "$RUN_COMMIT" "$RUN_PATH"

log "4. Download artifact ZIP without Git transport"
if ! download_artifact_zip "$DOWNLOAD_URL"; then
  if [ -z "$TOKEN" ]; then
    fail "Artifact download failed. Set AI_GITHUB_TOKEN to a fine-grained token with Actions: read, then rerun"
  fi
  fail "Artifact download failed with the configured GitHub token"
fi

if [[ "$ARTIFACT_DIGEST" == sha256:* ]]; then
  EXPECTED_ZIP_SHA="${ARTIFACT_DIGEST#sha256:}"
  ACTUAL_ZIP_SHA="$(sha256sum "$ARTIFACT_ZIP" | awk '{print $1}')"
  [ "$EXPECTED_ZIP_SHA" = "$ACTUAL_ZIP_SHA" ] || fail "GitHub artifact ZIP digest mismatch"
  printf 'artifactZipSha256=%s\n' "$ACTUAL_ZIP_SHA"
fi

log "5. Extract Actions artifact and verify sealed identity"
"$BOOTSTRAP_PYTHON" -m zipfile -e "$ARTIFACT_ZIP" "$ARTIFACT_DIR"
BUNDLE_ROOT="$ARTIFACT_DIR/ci-artifacts"
[ -d "$BUNDLE_ROOT" ] || fail "Formal artifact is missing ci-artifacts/"
mapfile -t BUNDLES < <(find "$BUNDLE_ROOT" -maxdepth 1 -type f -name "formal-release-${SOURCE_COMMIT}-*.tar.gz" -print | sort)
[ "${#BUNDLES[@]}" -eq 1 ] || fail "Expected exactly one formal-release-${SOURCE_COMMIT}-*.tar.gz in ci-artifacts, found ${#BUNDLES[@]}"
BUNDLE_TAR="${BUNDLES[0]}"
tar -xzf "$BUNDLE_TAR" -C "$CANDIDATE_DIR"
[ -f "$CANDIDATE_DIR/release/release-manifest.json" ] || fail "Sealed bundle manifest is missing"
[ -f "$CANDIDATE_DIR/release/attestation/test-attestation.json" ] || fail "Sealed test attestation is missing"
[ -f "$CANDIDATE_DIR/scripts/deploy_release.sh" ] || fail "Candidate deploy_release.sh is missing"
[ -f "$CANDIDATE_DIR/scripts/install_release_verifier.sh" ] || fail "Candidate verifier installer is missing"

SEALED_IDENTITY="$($BOOTSTRAP_PYTHON - \
  "$CANDIDATE_DIR/release/release-manifest.json" \
  "$CANDIDATE_DIR/release/attestation/test-attestation.json" \
  "$SOURCE_COMMIT" <<'PY'
import json
import sys

manifest_path, attestation_path, requested_sha = sys.argv[1:4]
with open(manifest_path, "r") as handle:
    manifest = json.load(handle)
with open(attestation_path, "r") as handle:
    attestation = json.load(handle)

manifest_commit = str(manifest.get("sourceCommit") or "")
attestation_commit = str(attestation.get("sourceCommit") or "")
checks = {
    "manifestCommit": manifest_commit,
    "attestationCommit": attestation_commit,
    "releaseEvent": str(attestation.get("releaseEvent") or ""),
    "releaseRef": str(attestation.get("releaseRef") or ""),
    "releaseBranch": str(attestation.get("releaseBranch") or ""),
    "releaseWorkflowPath": str(attestation.get("releaseWorkflowPath") or ""),
    "deployableArtifact": attestation.get("deployableArtifact"),
    "pullRequestMergeArtifact": attestation.get("pullRequestMergeArtifact"),
}
errors = []
if manifest_commit != requested_sha:
    errors.append("manifest sourceCommit mismatch")
if attestation_commit != requested_sha:
    errors.append("attestation sourceCommit mismatch")
if checks["releaseEvent"] != "push":
    errors.append("releaseEvent is not push")
if checks["releaseRef"] != "refs/heads/main":
    errors.append("releaseRef is not refs/heads/main")
if checks["releaseBranch"] != "main":
    errors.append("releaseBranch is not main")
if checks["releaseWorkflowPath"] != ".github/workflows/release-hash-seal.yml":
    errors.append("releaseWorkflowPath mismatch")
if checks["deployableArtifact"] is not True:
    errors.append("deployableArtifact is not true")
if checks["pullRequestMergeArtifact"] is not False:
    errors.append("pullRequestMergeArtifact is not false")
if errors:
    sys.stderr.write("; ".join(errors) + "\n")
    raise SystemExit(6)
print("\t".join([
    manifest_commit,
    attestation_commit,
    checks["releaseEvent"],
    checks["releaseRef"],
    checks["releaseBranch"],
    checks["releaseWorkflowPath"],
]))
PY
)" || fail "Sealed bundle is not a formal main-push release"

IFS=$'\t' read -r MANIFEST_COMMIT ATTESTATION_COMMIT SEALED_EVENT SEALED_REF SEALED_BRANCH SEALED_WORKFLOW_PATH <<< "$SEALED_IDENTITY"
[ "$MANIFEST_COMMIT" = "$RESOLVED_COMMIT" ] || fail "Artifact head SHA and sealed manifest sourceCommit differ"
[ "$MANIFEST_COMMIT" = "$RUN_COMMIT" ] || fail "Workflow run SHA and sealed manifest sourceCommit differ"
printf 'manifestSourceCommit=%s\nattestationSourceCommit=%s\nsealedEvent=%s\nsealedRef=%s\nsealedBranch=%s\nsealedWorkflowPath=%s\n' \
  "$MANIFEST_COMMIT" "$ATTESTATION_COMMIT" "$SEALED_EVENT" "$SEALED_REF" "$SEALED_BRANCH" "$SEALED_WORKFLOW_PATH"

log "6. Install or verify immutable root verifier"
bash "$CANDIDATE_DIR/scripts/install_release_verifier.sh"

log "7. Deploy exact sealed candidate with rollback protection"
AI_RELEASE_BUNDLE="$CANDIDATE_DIR" \
AI_ECOMMERCE_ROOT="$ROOT_DIR" \
AI_RELEASE_PYTHON="$RELEASE_PYTHON" \
bash "$CANDIDATE_DIR/scripts/deploy_release.sh"

log "8. Artifact transport completed"
printf 'repository=%s\nbranch=%s\nsourceCommit=%s\nartifactId=%s\nworkflowRunId=%s\nworkflowEvent=%s\nworkflowPath=%s\n' \
  "$REPOSITORY" "$FORMAL_BRANCH" "$RESOLVED_COMMIT" "$ARTIFACT_ID" "$RUN_ID" "$RUN_EVENT" "$RUN_PATH"
