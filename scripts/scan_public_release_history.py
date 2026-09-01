#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
import re
import subprocess
import sys


MAX_BLOB_BYTES = 5 * 1024 * 1024

STRONG_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "github_pat": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aliyun_access_key": re.compile(rb"\bLTAI[A-Za-z0-9]{12,24}\b"),
    "slack_token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}

KEY_VALUE_PATTERN = re.compile(
    rb"(?im)^(?:export\s+)?(?:[A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[:=]\s*['\"]?([^'\"\s#]{16,})"
)

SAFE_VALUE_MARKERS = (
    b"example",
    b"placeholder",
    b"changeme",
    b"your_",
    b"your-",
    b"dummy",
    b"fake",
    b"test",
    b"redacted",
    b"xxxx",
    b"<",
    b"${",
    b"{{",
)

SENSITIVE_BASENAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
SENSITIVE_SUFFIXES = {".pem", ".p12", ".pfx", ".key"}


def run_git(*args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.check_output(["git", *args], input=input_bytes)


def historical_objects() -> dict[str, set[str]]:
    paths_by_sha: dict[str, set[str]] = defaultdict(set)
    for raw in run_git("rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines():
        if not raw:
            continue
        parts = raw.split(" ", 1)
        sha = parts[0]
        if len(parts) == 2 and parts[1]:
            paths_by_sha[sha].add(parts[1])
        else:
            paths_by_sha.setdefault(sha, set())
    return paths_by_sha


def blob_metadata(shas: list[str]) -> dict[str, tuple[str, int]]:
    payload = ("\n".join(shas) + "\n").encode()
    output = run_git("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", input_bytes=payload)
    result: dict[str, tuple[str, int]] = {}
    for line in output.decode().splitlines():
        sha, kind, size = line.split()
        result[sha] = (kind, int(size))
    return result


def suspicious_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name in SENSITIVE_BASENAMES:
        return True
    return pure.suffix.lower() in SENSITIVE_SUFFIXES


def looks_placeholder(value: bytes) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in SAFE_VALUE_MARKERS)


def line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def main() -> int:
    objects = historical_objects()
    metadata = blob_metadata(list(objects))
    findings: list[tuple[str, str, str, int | None]] = []
    scanned_blobs = 0
    skipped_large_blobs = 0

    for sha, paths in objects.items():
        kind, size = metadata.get(sha, ("", 0))
        if kind != "blob":
            continue
        if size > MAX_BLOB_BYTES:
            skipped_large_blobs += 1
            continue
        data = run_git("cat-file", "blob", sha)
        scanned_blobs += 1
        display_paths = sorted(paths) or ["<unresolved-path>"]

        for path in display_paths:
            if suspicious_path(path):
                findings.append(("sensitive_filename", sha, path, None))

        if b"\x00" in data[:8192]:
            continue

        primary_path = display_paths[0]
        for label, pattern in STRONG_PATTERNS.items():
            for match in pattern.finditer(data):
                findings.append((label, sha, primary_path, line_number(data, match.start())))

        for match in KEY_VALUE_PATTERN.finditer(data):
            value = match.group(1)
            if looks_placeholder(value):
                continue
            if len(set(value)) < 6:
                continue
            findings.append(("credential_assignment", sha, primary_path, line_number(data, match.start())))

    print(f"HISTORY_OBJECT_COUNT={len(objects)}")
    print(f"HISTORY_BLOB_SCANNED={scanned_blobs}")
    print(f"HISTORY_LARGE_BLOB_SKIPPED={skipped_large_blobs}")

    unique = sorted(set(findings))
    if unique:
        print("PUBLIC_RELEASE_HISTORY_SECRET_FINDINGS:")
        for label, sha, path, line in unique:
            location = f"{path}:{line}" if line is not None else path
            print(f"- {label} {sha} {location}")
        return 1

    print("PUBLIC_RELEASE_HISTORY_SECRET_SCAN=verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
