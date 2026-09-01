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
    "provider_sk_token": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
}

CREDENTIAL_NAME = (
    rb"[A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*"
)

# Generic credential detection is intentionally literal-only. Runtime expressions such as
# TOKEN="${GITHUB_TOKEN:-}", token = sha256(...), api_key = os.getenv(...), or values read
# from `gh auth` / git credential helpers are not repository-embedded credentials.
QUOTED_CREDENTIAL_PATTERN = re.compile(
    rb"(?im)^[ \t]*(?:export[ \t]+)?(" + CREDENTIAL_NAME + rb")"
    rb"[ \t]*[:=][ \t]*(['\"])([^'\"\r\n]{16,})\2[ \t]*(?:[,;]?[ \t]*(?:#.*)?)?$",
    re.I,
)
UNQUOTED_CREDENTIAL_PATTERN = re.compile(
    rb"(?im)^[ \t]*(?:export[ \t]+)?(" + CREDENTIAL_NAME + rb")"
    rb"[ \t]*[:=][ \t]*([A-Za-z0-9_./+=:@-]{16,})[ \t]*(?:#.*)?$",
    re.I,
)
JSON_CREDENTIAL_PATTERN = re.compile(
    rb"(?im)^[ \t]*['\"](" + CREDENTIAL_NAME + rb")['\"]"
    rb"[ \t]*:[ \t]*(['\"])([^'\"\r\n]{16,})\2[ \t]*,?[ \t]*$",
    re.I,
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
    b"$(",
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

UPPER_IDENTIFIER = re.compile(rb"^[A-Z][A-Z0-9_]{7,}$")
DESCRIPTIVE_SNAKE_CASE = re.compile(rb"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+){2,}$")
URL_VALUE = re.compile(rb"^https?://", re.I)
VERSION_VALUE = re.compile(rb"^v?\d+(?:\.\d+){1,4}(?:[-+][A-Za-z0-9_.-]+)?$", re.I)
HEX_DIGEST = re.compile(rb"^[0-9a-f]{32,128}$", re.I)


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


def probable_literal_secret(value: bytes) -> bool:
    value = value.strip().rstrip(b";,)")
    if not value or looks_placeholder(value):
        return False
    if URL_VALUE.match(value) or VERSION_VALUE.match(value):
        return False
    if value.lower().startswith((b"sha256:", b"sha1:", b"md5:")) or HEX_DIGEST.fullmatch(value):
        return False
    if UPPER_IDENTIFIER.fullmatch(value) or DESCRIPTIVE_SNAKE_CASE.fullmatch(value):
        return False
    if len(set(value)) < 8:
        return False

    has_lower = any(97 <= ch <= 122 for ch in value)
    has_upper = any(65 <= ch <= 90 for ch in value)
    has_digit = any(48 <= ch <= 57 for ch in value)
    has_symbol = any(not (48 <= ch <= 57 or 65 <= ch <= 90 or 97 <= ch <= 122) for ch in value)
    classes = sum((has_lower, has_upper, has_digit, has_symbol))
    return len(value) >= 20 and classes >= 2


def line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def literal_credential_matches(data: bytes):
    for pattern, value_group in (
        (QUOTED_CREDENTIAL_PATTERN, 3),
        (UNQUOTED_CREDENTIAL_PATTERN, 2),
        (JSON_CREDENTIAL_PATTERN, 3),
    ):
        for match in pattern.finditer(data):
            value = match.group(value_group).strip()
            if probable_literal_secret(value):
                yield match


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

        for match in literal_credential_matches(data):
            findings.append(("credential_literal", sha, primary_path, line_number(data, match.start())))

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
