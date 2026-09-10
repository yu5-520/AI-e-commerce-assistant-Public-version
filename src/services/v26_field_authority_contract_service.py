"""V26.1 field-header authority contract.

Authority validation is deliberately structural.  It validates who may read/write a
registered field header, value type/range when declared, and reference namespace.
It does not inspect, require, rewrite or score business wording inside SEMANTIC
fields.  That separation lets the control plane stay strict while the LLM semantic
surface remains active.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

V26_FIELD_AUTHORITY_VERSION = "26.1.0"
DEFAULT_CONTRACT_PATH = Path("config/v26_field_authority_contract.json")


class FieldAuthorityViolation(ValueError):
    """Fail-closed V26.1 field authority violation."""


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise FieldAuthorityViolation("v26_field_authority_contract_not_object")
    return value


class V26FieldAuthorityContract:
    def __init__(self, contract_path: str | Path | None = None) -> None:
        self.path = Path(contract_path) if contract_path else DEFAULT_CONTRACT_PATH
        self.contract = _load_json(self.path)
        if str(self.contract.get("version") or "") != V26_FIELD_AUTHORITY_VERSION:
            raise FieldAuthorityViolation("v26_field_authority_version_mismatch")
        if self.contract.get("status") != "active":
            raise FieldAuthorityViolation("v26_field_authority_not_active")
        self._namespace_policies = sorted(
            [item for item in self.contract.get("namespacePolicies", []) if isinstance(item, dict)],
            key=lambda item: len(str(item.get("prefix") or "")),
            reverse=True,
        )
        self._header_overrides = {
            str(key): value
            for key, value in dict(self.contract.get("headerOverrides") or {}).items()
            if isinstance(value, dict)
        }

    def resolve_policy(self, header: str) -> Dict[str, Any]:
        header = str(header or "").strip()
        if not header or "." not in header:
            raise FieldAuthorityViolation(f"v26_field_header_invalid:{header}")
        base: Dict[str, Any] = {}
        for item in self._namespace_policies:
            prefix = str(item.get("prefix") or "")
            if prefix and header.startswith(prefix):
                base = dict(item)
                break
        if not base:
            raise FieldAuthorityViolation(f"v26_field_header_unregistered:{header}")
        override = self._header_overrides.get(header)
        if override:
            base.update(override)
        base["header"] = header
        return base

    @staticmethod
    def _assert_value_type(header: str, value: Any, policy: Mapping[str, Any]) -> None:
        declared = str(policy.get("valueType") or "").strip().lower()
        if not declared:
            return
        valid = True
        if declared == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif declared == "string":
            valid = isinstance(value, str)
        elif declared == "array":
            valid = isinstance(value, list)
        elif declared == "object":
            valid = isinstance(value, dict)
        elif declared == "boolean":
            valid = isinstance(value, bool)
        if not valid:
            raise FieldAuthorityViolation(f"v26_field_type_mismatch:{header}:{declared}")
        if declared == "number":
            if policy.get("minimum") is not None and value < policy["minimum"]:
                raise FieldAuthorityViolation(f"v26_field_below_minimum:{header}")
            if policy.get("maximum") is not None and value > policy["maximum"]:
                raise FieldAuthorityViolation(f"v26_field_above_maximum:{header}")

    def assert_read(self, actor: str, header: str) -> Dict[str, Any]:
        policy = self.resolve_policy(header)
        if str(actor or "") not in set(policy.get("readableBy") or []):
            raise FieldAuthorityViolation(f"v26_field_read_forbidden:{actor}:{header}")
        return policy

    def assert_write(self, actor: str, header: str, value: Any) -> Dict[str, Any]:
        actor = str(actor or "").strip()
        policy = self.resolve_policy(header)
        if actor not in set(policy.get("writableBy") or []):
            raise FieldAuthorityViolation(f"v26_field_write_forbidden:{actor}:{header}")
        self._assert_value_type(header, value, policy)

        field_class = str(policy.get("class") or "")
        if field_class == "SEMANTIC" and isinstance(value, str):
            max_len = int(
                dict(self.contract.get("fieldClasses") or {})
                .get("SEMANTIC", {})
                .get("maxTextLength", 12000)
            )
            if len(value) > max_len:
                raise FieldAuthorityViolation(f"v26_semantic_field_too_long:{header}")
            # Intentionally no keyword, phrase, business-domain or numeric-content
            # inspection here.  Header ownership is the authority boundary.

        if field_class == "REFERENCE":
            expected_prefix = str(policy.get("referenceNamespace") or "")
            refs: Iterable[Any] = value if isinstance(value, list) else [value]
            for ref in refs:
                if not isinstance(ref, str) or not ref.strip():
                    raise FieldAuthorityViolation(f"v26_reference_invalid:{header}")
                if expected_prefix and not ref.startswith(expected_prefix):
                    raise FieldAuthorityViolation(
                        f"v26_reference_namespace_mismatch:{header}:{ref}"
                    )
        return policy

    def assert_payload_write(self, actor: str, payload: Mapping[str, Any]) -> None:
        """Validate an already-flattened header/value payload fail-closed."""
        for header, value in payload.items():
            self.assert_write(actor, str(header), value)

    def assert_stage_write(self, actor: str, stage_kind: str) -> None:
        stage_authority = dict(self.contract.get("stageAuthority") or {})
        stage_kind = str(stage_kind or "").strip()
        policy = dict(stage_authority.get(stage_kind) or {})
        if not policy:
            raise FieldAuthorityViolation(f"v26_stage_kind_unregistered:{stage_kind}")
        if stage_kind == "systemStage":
            if str(actor or "") not in {"system", "java-control-plane"}:
                raise FieldAuthorityViolation(f"v26_system_stage_write_forbidden:{actor}")
            return
        allowed = set(policy.get("modelWritableBy") or []) | {"system", "java-control-plane"}
        if str(actor or "") not in allowed:
            raise FieldAuthorityViolation(f"v26_operation_stage_write_forbidden:{actor}")

    def receipt(self) -> Dict[str, Any]:
        return {
            "version": V26_FIELD_AUTHORITY_VERSION,
            "contractPath": str(self.path),
            "headerAuthority": True,
            "semanticBusinessWordingInspected": False,
            "systemStageModelWritable": False,
            "operationStageAgent3Writable": True,
            "agent3PlanNumberMode": "reference_only",
        }


__all__ = [
    "V26_FIELD_AUTHORITY_VERSION",
    "FieldAuthorityViolation",
    "V26FieldAuthorityContract",
]
