#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

EXPECTED_BUNDLE_SHA256 = "8c46ca37519698399d0aaf93c1c970cff2472876b208f5ac561c5c3a4d6fa438"
EXPECTED_SOURCE_COMMIT = "f5186451c80631fea550da17d481f5e8793215e5"
EXPECTED_RELEASE_HASH = "sha256:593b94a045c0532738ff2da0ed18ccd44179d425fac08cd8542203a644bc4d26"
EXPECTED_MANIFEST_HASH = "sha256:b019ceb2c1fff35e7ebb34ca5ebab4d13011e8eab95b32ff0e2bdb76a66cc6f3"
CONTROL_WORKFLOW = ".github/workflows/import-precise-release-self-hosted.yml"
CONTROL_SCRIPT = "scripts/import_precise_release_via_api.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        if not members:
            raise RuntimeError("release bundle is empty")
        for member in members:
            rel = PurePosixPath(member.name)
            if rel.is_absolute() or ".." in rel.parts:
                raise RuntimeError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeError(f"unsupported archive member: {member.name}")
            target = (destination / Path(*rel.parts)).resolve()
            if target != destination and destination not in target.parents:
                raise RuntimeError(f"archive member escapes destination: {member.name}")
        handle.extractall(destination)


class GitHubAPI:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.root = f"https://api.github.com/repos/{repo}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-ecommerce-public-release-importer",
        }

    def call(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        accepted: tuple[int, ...] = (200, 201, 204),
        attempts: int = 10,
    ) -> Any:
        body = None
        headers = dict(self.headers)
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    raw = response.read()
                    if response.status not in accepted:
                        raise RuntimeError(f"unexpected status {response.status}: {url}")
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                retryable = exc.code in {403, 408, 409, 422, 429, 500, 502, 503, 504}
                if retryable and attempt < attempts:
                    print(f"API retry {attempt}/{attempts}: HTTP {exc.code} {method} {url}", flush=True)
                    time.sleep(min(30, attempt * 3))
                    continue
                raise RuntimeError(f"GitHub API failed: {method} {url} HTTP {exc.code}: {raw}") from exc
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                if attempt >= attempts:
                    raise RuntimeError(f"GitHub API network failure: {method} {url}: {exc}") from exc
                print(f"network retry {attempt}/{attempts}: {method} {url}: {exc}", flush=True)
                time.sleep(min(30, attempt * 3))
        raise RuntimeError("unreachable")

    def upload_blob(self, path: Path) -> str:
        data = path.read_bytes()
        result = self.call(
            "POST",
            f"{self.root}/git/blobs",
            {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
            accepted=(201,),
        )
        return str(result["sha"])


def validate_parent_release(extracted: Path) -> dict[str, Any]:
    manifest_path = extracted / "release" / "release-manifest.json"
    verifier_path = extracted / "scripts" / "release_verifier.py"
    if not manifest_path.is_file() or not verifier_path.is_file():
        raise RuntimeError("release manifest or verifier missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "sourceCommit": EXPECTED_SOURCE_COMMIT,
        "releaseHash": EXPECTED_RELEASE_HASH,
        "manifestHash": EXPECTED_MANIFEST_HASH,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RuntimeError(f"parent release {key} mismatch: {manifest.get(key)!r}")
    receipt_path = extracted / "release" / "import-verification.json"
    completed = subprocess.run(
        [sys.executable, str(verifier_path), "--root", str(extracted), "--manifest", str(manifest_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    receipt_path.write_text(completed.stdout, encoding="utf-8")
    receipt = json.loads(completed.stdout)
    if receipt.get("verified") is not True:
        raise RuntimeError("parent release verifier did not pass")
    return manifest


def prepare_staging(extracted: Path, checkout: Path, staging: Path, bundle_digest: str) -> None:
    shutil.copytree(extracted, staging, dirs_exist_ok=True)

    source_workflows = staging / ".github" / "workflows"
    quarantine = staging / "governance" / "quarantine" / "source-workflows"
    if source_workflows.exists():
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        if quarantine.exists():
            shutil.rmtree(quarantine)
        shutil.move(str(source_workflows), str(quarantine))

    for relative in (CONTROL_WORKFLOW, CONTROL_SCRIPT):
        source = checkout / relative
        target = staging / relative
        if not source.is_file():
            raise RuntimeError(f"control file missing from checkout: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    lineage_dir = staging / "governance" / "import"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    lineage = {
        "schema": "public.release-lineage.v1",
        "profile": "public_stable",
        "state": "IMPORTED_UNPRUNED",
        "publicationAllowed": False,
        "source": {
            "repository": "yu5-520/AI-e-commerce-assistant",
            "commit": EXPECTED_SOURCE_COMMIT,
            "artifactId": 8815984381,
            "artifactDigest": "sha256:b2c9892f3bcf066fabebe52c97aa6acd0b49c0316c40d2c909c0917bbb13f66f",
            "bundleDigest": f"sha256:{bundle_digest}",
        },
        "lineage": {
            "parentReleaseHash": EXPECTED_RELEASE_HASH,
            "parentManifestHash": EXPECTED_MANIFEST_HASH,
            "derivedReleaseHash": None,
            "derivedManifestHash": None,
        },
        "processing": {
            "parentReleaseVerified": True,
            "sourceWorkflowsQuarantined": True,
            "registryPruningCompleted": False,
            "reverseDependencyVerificationCompleted": False,
            "publicSafetyScanCompleted": False,
        },
    }
    (lineage_dir / "parent-lineage.json").write_text(
        json.dumps(lineage, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (lineage_dir / "PUBLICATION_BLOCKED.md").write_text(
        "# Publication blocked\n\n"
        "This private repository contains the verified parent release bundle. "
        "Unified-registry pruning, reverse-dependency checks, public safety review, "
        "and derived rehashing are not complete. Do not make the repository public.\n",
        encoding="utf-8",
    )


def collect_files(staging: Path) -> list[tuple[str, Path, str]]:
    result: list[tuple[str, Path, str]] = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(staging).as_posix()
        mode = "100755" if os.access(path, os.X_OK) else "100644"
        result.append((relative, path, mode))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--parent-commit", required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")

    bundle = Path(args.bundle).resolve()
    checkout = Path.cwd().resolve()
    if not bundle.is_file():
        raise RuntimeError(f"bundle not found: {bundle}")
    digest = sha256_file(bundle)
    print(f"bundle sha256: {digest}", flush=True)
    if digest != EXPECTED_BUNDLE_SHA256:
        raise RuntimeError("bundle SHA256 mismatch")

    api = GitHubAPI(args.repo, token)
    repository = api.call("GET", api.root)
    if repository.get("private") is not True:
        raise RuntimeError("target repository must remain private")

    with tempfile.TemporaryDirectory(prefix="public-release-import-") as temp:
        temp_root = Path(temp)
        extracted = temp_root / "extracted"
        staging = temp_root / "staging"
        extracted.mkdir()
        safe_extract(bundle, extracted)
        manifest = validate_parent_release(extracted)
        print(f"parent release verified: {manifest['releaseHash']}", flush=True)
        prepare_staging(extracted, checkout, staging, digest)
        files = collect_files(staging)
        print(f"files to import: {len(files)}", flush=True)

        uploaded: dict[str, tuple[str, str]] = {}
        completed = 0

        def task(item: tuple[str, Path, str]) -> tuple[str, str, str]:
            relative, path, mode = item
            return relative, api.upload_blob(path), mode

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(task, item): item[0] for item in files}
            for future in concurrent.futures.as_completed(futures):
                relative, sha, mode = future.result()
                uploaded[relative] = (sha, mode)
                completed += 1
                print(f"[{completed}/{len(files)}] {relative}", flush=True)

        tree = api.call(
            "POST",
            f"{api.root}/git/trees",
            {
                "tree": [
                    {"path": path, "mode": mode, "type": "blob", "sha": sha}
                    for path, (sha, mode) in sorted(uploaded.items())
                ]
            },
            accepted=(201,),
        )
        commit = api.call(
            "POST",
            f"{api.root}/git/commits",
            {
                "message": (
                    "bootstrap(public): import verified parent release bundle\n\n"
                    f"Source commit: {EXPECTED_SOURCE_COMMIT}\n"
                    f"Parent release hash: {EXPECTED_RELEASE_HASH}\n"
                    "State: IMPORTED_UNPRUNED; publication blocked"
                ),
                "tree": tree["sha"],
                "parents": [args.parent_commit],
            },
            accepted=(201,),
        )
        api.call(
            "PATCH",
            f"{api.root}/git/refs/heads/{args.ref}",
            {"sha": commit["sha"], "force": False},
            accepted=(200,),
        )
        final = api.call("GET", f"{api.root}/git/commits/{commit['sha']}")
        if final["tree"]["sha"] != tree["sha"]:
            raise RuntimeError("final remote tree mismatch")
        print("=" * 72)
        print("PRECISE_RELEASE_IMPORT=PASS")
        print(f"REMOTE_COMMIT={commit['sha']}")
        print(f"REMOTE_TREE={tree['sha']}")
        print(f"FILE_COUNT={len(files)}")
        print(f"PARENT_RELEASE_HASH={EXPECTED_RELEASE_HASH}")
        print("PUBLICATION_ALLOWED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
