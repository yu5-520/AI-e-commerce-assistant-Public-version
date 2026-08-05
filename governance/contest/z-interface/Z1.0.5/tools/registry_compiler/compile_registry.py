"""Repository-neutral deterministic Registry compiler for Z-Century."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Sequence

REGISTRY_VERSION = "Z1.0.5"
REGISTRY_SPECS = (
    ("fields.json", "fields", "fieldId"),
    ("schemas.json", "schemas", "schemaId"),
    ("interfaces.json", "interfaces", "interfaceId"),
    ("modules.json", "modules", "moduleId"),
    ("ownership.json", "ownership", "ownershipId"),
    ("migrations.json", "migrations", "migrationId"),
    ("stations.json", "stations", "stationId"),
    ("tombstones.json", "tombstones", "tombstoneId"),
)

class RegistryCompileError(RuntimeError):
    pass

def repository_root(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parents[2]).resolve()

def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")

def sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()

def _read_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RegistryCompileError(f"REGISTRY_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise RegistryCompileError(f"REGISTRY_OBJECT_REQUIRED:{path}")
    return value

def load_registry_documents(root: Path | None = None) -> Dict[str, Dict[str, Any]]:
    repository = repository_root(root)
    directory = repository / "contracts" / "registry"
    documents: Dict[str, Dict[str, Any]] = {}
    for filename, record_key, identity_key in REGISTRY_SPECS:
        path = directory / filename
        document = _read_object(path)
        records = document.get(record_key)
        if not isinstance(records, list):
            raise RegistryCompileError(f"REGISTRY_RECORD_LIST_REQUIRED:{filename}:{record_key}")
        seen: set[str] = set()
        for raw in records:
            if not isinstance(raw, dict):
                raise RegistryCompileError(f"REGISTRY_RECORD_OBJECT_REQUIRED:{filename}")
            identity = str(raw.get(identity_key) or "").strip()
            if not identity:
                raise RegistryCompileError(f"REGISTRY_ID_REQUIRED:{filename}:{identity_key}")
            if identity in seen:
                raise RegistryCompileError(f"REGISTRY_DUPLICATE_ID:{filename}:{identity}")
            seen.add(identity)
        documents[filename] = document
    return documents

def compile_registry(root: Path | None = None, *, write: bool = True) -> Dict[str, Any]:
    repository = repository_root(root)
    documents = load_registry_documents(repository)
    registry_files = [
        {"path": f"contracts/registry/{filename}", "contentHash": sha256_value(documents[filename])}
        for filename, _, _ in REGISTRY_SPECS
    ]
    material = {
        "schema": "registry.manifest.v1",
        "version": REGISTRY_VERSION,
        "mode": "bootstrap_source_fail_closed",
        "registryFiles": registry_files,
    }
    manifest = {**material, "generatedBy": "tools.registry_compiler.compile_registry", "registryRootHash": sha256_value(material)}
    if write:
        path = repository / "contracts" / "registry" / "registry-manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile the Z Registry manifest.")
    parser.add_argument("--root")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    repository = repository_root(Path(args.root) if args.root else None)
    generated = compile_registry(repository, write=not args.check)
    if args.check:
        current = _read_object(repository / "contracts" / "registry" / "registry-manifest.json")
        if current != generated:
            print(json.dumps({"verified": False, "reason": "REGISTRY_MANIFEST_DRIFT"}))
            return 2
    print(json.dumps({"verified": True, "registryRootHash": generated["registryRootHash"], "registryFileCount": len(generated["registryFiles"])}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["RegistryCompileError", "canonical_bytes", "compile_registry", "load_registry_documents", "repository_root", "sha256_value"]
