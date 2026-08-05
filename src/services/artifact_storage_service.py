"""Local immutable artifact storage with atomic writes and content hashing.

The MVP uses the repository data directory. Future OSS/S3/MinIO adapters only need
to preserve this service contract; Agent workers do not know the storage backend.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from src.runtime_version import VERSION

ARTIFACT_STORAGE_VERSION = "22.2.1"
ROOT_DIR = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = Path(os.getenv("ARTIFACT_ROOT", str(ROOT_DIR / "data" / "artifacts")))


def ensure_artifact_storage() -> Path:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_ROOT


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def content_hash(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _safe_segment(value: str | None, fallback: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or ""))
    return text.strip("._")[:100] or fallback


def _relative_path(artifact_type: str, artifact_id: str, extension: str) -> Path:
    family = _safe_segment(artifact_type, "generic")
    name = _safe_segment(artifact_id, "artifact")
    suffix = extension if extension.startswith(".") else "." + extension
    return Path(family) / name[:2] / f"{name}{suffix}"


def write_bytes(
    *,
    artifact_type: str,
    artifact_id: str,
    content: bytes,
    extension: str = ".bin",
) -> Dict[str, Any]:
    root = ensure_artifact_storage()
    relative = _relative_path(artifact_type, artifact_id, extension)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = content_hash(content)

    if target.exists():
        current = target.read_bytes()
        if content_hash(current) != digest:
            raise RuntimeError(f"immutable_artifact_content_conflict:{artifact_id}")
        return {
            "version": VERSION,
            "storageUri": f"artifact://local/{relative.as_posix()}",
            "absolutePath": str(target),
            "contentHash": digest,
            "sizeBytes": len(current),
            "idempotentHit": True,
        }

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)

    return {
        "version": VERSION,
        "storageUri": f"artifact://local/{relative.as_posix()}",
        "absolutePath": str(target),
        "contentHash": digest,
        "sizeBytes": len(content),
        "idempotentHit": False,
    }


def write_json(
    *,
    artifact_type: str,
    artifact_id: str,
    value: Any,
) -> Dict[str, Any]:
    return write_bytes(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        content=canonical_json_bytes(value),
        extension=".json",
    )


def resolve_storage_uri(storage_uri: str) -> Path:
    prefix = "artifact://local/"
    if not str(storage_uri or "").startswith(prefix):
        raise ValueError("unsupported_artifact_storage_uri")
    relative = Path(str(storage_uri)[len(prefix):])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("invalid_artifact_storage_uri")
    target = ensure_artifact_storage() / relative
    return target


def read_bytes(storage_uri: str) -> bytes:
    path = resolve_storage_uri(storage_uri)
    if not path.exists():
        raise FileNotFoundError(f"artifact_content_missing:{storage_uri}")
    return path.read_bytes()


def read_json(storage_uri: str) -> Any:
    return json.loads(read_bytes(storage_uri).decode("utf-8"))


__all__ = [
    "ARTIFACT_STORAGE_VERSION",
    "ARTIFACT_ROOT",
    "ensure_artifact_storage",
    "canonical_json_bytes",
    "content_hash",
    "write_bytes",
    "write_json",
    "read_bytes",
    "read_json",
    "resolve_storage_uri",
]
