"""Verify the product Registry through an explicit report-only Z protocol adapter."""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("_contest_z_registry_protocol", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_document(
    document: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> Dict[str, Any]:
    source_collection = str(mapping.get("sourceCollection") or "")
    target_collection = str(mapping.get("targetCollection") or "")
    source_identity = str(mapping.get("sourceIdentity") or "")
    target_identity = str(mapping.get("targetIdentity") or "")
    mode = str(mapping.get("mappingMode") or "")
    prefix = str(mapping.get("identityPrefix") or "")

    records = document.get(source_collection)
    if not isinstance(records, list):
        raise RuntimeError(
            f"SOURCE_COLLECTION_REQUIRED:{mapping.get('path')}:{source_collection}"
        )

    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise RuntimeError(f"SOURCE_RECORD_OBJECT_REQUIRED:{mapping.get('path')}")
        source_id = str(raw.get(source_identity) or "").strip()
        if not source_id:
            raise RuntimeError(
                f"SOURCE_ID_REQUIRED:{mapping.get('path')}:{source_identity}"
            )
        if mode == "PASSTHROUGH":
            target_id = source_id
            record = dict(raw)
        elif mode == "COLLECTION_AND_IDENTITY_ADAPTER":
            target_id = prefix + source_id
            record = {**raw, target_identity: target_id}
        else:
            raise RuntimeError(f"MAPPING_MODE_UNSUPPORTED:{mode}")
        if target_id in seen:
            raise RuntimeError(f"TARGET_ID_DUPLICATE:{mapping.get('path')}:{target_id}")
        seen.add(target_id)
        normalized.append(record)

    result = {
        key: value
        for key, value in document.items()
        if key != source_collection
    }
    result[target_collection] = normalized
    return result


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Product Registry → Z Protocol Review",
        "",
        f"- State: `{report.get('state')}`",
        f"- Adapter hash: `{report.get('adapterHash')}`",
        f"- Normalized Registry root: `{report.get('normalizedRegistryRootHash')}`",
        f"- Source document hashes verified: `{report.get('sourceDocumentHashesVerified')}`",
        f"- Z compiler verified: `{report.get('zCompilerVerified')}`",
        "",
        "## Document mappings",
        "",
        "| Document | Mapping | Source collection/id | Target collection/id | Records |",
        "|---|---|---|---|---:|",
    ]
    for item in report.get("documentReviews") or []:
        lines.append(
            "| `{path}` | `{mode}` | `{source_collection}/{source_id}` | "
            "`{target_collection}/{target_id}` | {count} |".format(
                path=item.get("path"),
                mode=item.get("mappingMode"),
                source_collection=item.get("sourceCollection"),
                source_id=item.get("sourceIdentity"),
                target_collection=item.get("targetCollection"),
                target_id=item.get("targetIdentity"),
                count=item.get("recordCount"),
            )
        )
    lines.extend(
        [
            "",
            "## Authority boundary",
            "",
            "The normalized documents exist only in an ephemeral verification directory.",
            "The product Registry, source manifest, runtime projection, database, provider",
            "state, and deployed runtime are unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def run(root: Path, output_dir: Path) -> Dict[str, Any]:
    contract = _read_object(root / "governance/contest/registry-protocol-map.json")
    layout = _read_object(root / ".z/adapter/registry-layout.json")
    interface_path = (
        root
        / "governance/contest/z-interface/Z1.0.5/tools/registry_compiler/compile_registry.py"
    )
    z_compile = _load_module(interface_path)

    expected_hashes = {
        str(item.get("path") or ""): str(item.get("contentHash") or "")
        for item in layout.get("documents") or []
        if isinstance(item, dict)
    }
    document_reviews: List[Dict[str, Any]] = []
    normalized_documents: Dict[str, Dict[str, Any]] = {}
    all_source_hashes_match = True

    for mapping in contract.get("documents") or []:
        if not isinstance(mapping, dict):
            continue
        filename = str(mapping.get("path") or "")
        source_path = root / "contracts/registry" / filename
        source = _read_object(source_path)
        actual_source_hash = str(z_compile.sha256_value(source))
        expected_source_hash = expected_hashes.get(
            f"contracts/registry/{filename}", ""
        )
        source_hash_matches = bool(expected_source_hash) and (
            actual_source_hash == expected_source_hash
        )
        all_source_hashes_match = all_source_hashes_match and source_hash_matches
        normalized = _normalize_document(source, mapping)
        normalized_documents[filename] = normalized
        records = normalized.get(str(mapping.get("targetCollection") or "")) or []
        document_reviews.append(
            {
                "path": filename,
                "mappingMode": mapping.get("mappingMode"),
                "sourceCollection": mapping.get("sourceCollection"),
                "targetCollection": mapping.get("targetCollection"),
                "sourceIdentity": mapping.get("sourceIdentity"),
                "targetIdentity": mapping.get("targetIdentity"),
                "recordCount": len(records),
                "sourceContentHash": actual_source_hash,
                "expectedSourceContentHash": expected_source_hash,
                "sourceHashMatches": source_hash_matches,
                "normalizedContentHash": z_compile.sha256_value(normalized),
            }
        )

    if len(normalized_documents) != 8:
        raise RuntimeError(
            f"NORMALIZED_DOCUMENT_COUNT_MISMATCH:{len(normalized_documents)}"
        )
    if not all_source_hashes_match:
        raise RuntimeError("SOURCE_DOCUMENT_HASH_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="contest-z-registry-") as temporary:
        temporary_root = Path(temporary)
        registry_dir = temporary_root / "contracts/registry"
        registry_dir.mkdir(parents=True, exist_ok=True)
        for filename, document in sorted(normalized_documents.items()):
            _write_json(registry_dir / filename, document)
        generated = z_compile.compile_registry(temporary_root, write=False)

    adapter_material = {
        "schema": "contest.registry_protocol_adapter_review.v1",
        "mode": "report_only",
        "sourceAuthority": contract.get("sourceAuthority"),
        "targetInterface": contract.get("targetInterface"),
        "mappingContractHash": z_compile.sha256_value(contract),
        "sourceDocumentHashesVerified": all_source_hashes_match,
        "zCompilerVerified": True,
        "normalizedRegistryRootHash": generated.get("registryRootHash"),
        "normalizedRegistryFileCount": len(generated.get("registryFiles") or []),
        "documentReviews": document_reviews,
        "constraints": contract.get("constraints"),
        "state": "PROTOCOL_ADAPTER_VERIFIED_RUNTIME_SWITCH_NOT_AUTHORIZED",
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
        "normalizedDocumentsPersistedToRuntime": False,
    }
    report = {
        **adapter_material,
        "adapterHash": z_compile.sha256_value(adapter_material),
    }
    receipt_material = {
        "schema": "contest.registry_protocol_adapter_receipt.v1",
        "adapterHash": report["adapterHash"],
        "normalizedRegistryRootHash": report["normalizedRegistryRootHash"],
        "sourceDocumentHashesVerified": True,
        "zCompilerVerified": True,
        "states": [
            "SOURCE_DOCUMENT_HASHES_VERIFIED",
            "PRODUCT_PROTOCOL_MAPPED_IN_MEMORY",
            "Z_COMPILER_VERIFIED",
            "RUNTIME_SWITCH_NOT_AUTHORIZED",
        ],
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    receipt = {
        **receipt_material,
        "receiptHash": z_compile.sha256_value(receipt_material),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "registry-protocol-adapter-review.json", report)
    _write_json(output_dir / "registry-protocol-adapter-receipt.json", receipt)
    (output_dir / "registry-protocol-adapter-review.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    return {
        "adapterHash": report["adapterHash"],
        "receiptHash": receipt["receiptHash"],
        "normalizedRegistryRootHash": report["normalizedRegistryRootHash"],
        "documentCount": len(document_reviews),
        "state": report["state"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the product Registry through the pinned Z interface."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="governance/contest/generated")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).resolve()
    result = run(root, (root / args.output).resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
