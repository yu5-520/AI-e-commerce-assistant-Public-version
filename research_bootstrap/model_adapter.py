from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from core import sha256_json


class ModelAdapter(Protocol):
    model_id: str
    model_version: str | None
    adapter_version: str

    def generate_neutral(self, model_view: Dict[str, Any]) -> Dict[str, Any]:
        """Generate exactly once from model-visible content.

        The adapter MUST NOT receive bias labels, expected authority, condition names,
        fixture proposals, or evaluation-only metadata.
        """
        ...


def assert_model_view_clean(model_view: Dict[str, Any]) -> None:
    rendered = str(model_view).lower()
    forbidden = (
        "bias_family",
        "expected_primary_authority",
        "fixture_proposal",
        "information_authority",
        "invocation_authority",
        "temporal_authority",
        "matched authority",
        "reality bias",
        "authority penetration",
    )
    for token in forbidden:
        if token in rendered:
            raise AssertionError(f"evaluation leakage into model adapter: {token}")


@dataclass(frozen=True)
class FixtureNeutralAdapter:
    """Zero-cost adapter for CI only; never used as empirical model evidence."""

    model_id: str = "fixture-neutral"
    model_version: str | None = "v1"
    adapter_version: str = "neutral-contract-v1"

    def generate_neutral(self, model_view: Dict[str, Any]) -> Dict[str, Any]:
        assert_model_view_clean(model_view)
        return {
            "provider": "fixture",
            "model_id": self.model_id,
            "model_version": self.model_version,
            "adapter_version": self.adapter_version,
            "proposal_protocol_version": "fixture-proposal-v1",
            "input_hash": sha256_json(model_view),
            "structured_output": {"assessment": "", "effects": []},
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "raw_response_hash": None,
            "raw_response_ref": None,
        }


def freeze_generation_record(*, case_id: str, model_view: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    assert_model_view_clean(model_view)
    structured = response.get("structured_output")
    if not isinstance(structured, dict):
        raise ValueError("structured_output must be an object")
    record = {
        "case_id": case_id,
        "provider": response.get("provider"),
        "model_id": response.get("model_id"),
        "model_version": response.get("model_version"),
        "adapter_version": response.get("adapter_version"),
        "proposal_protocol_version": response.get("proposal_protocol_version"),
        "model_input_hash": sha256_json(model_view),
        "provider_input_hash": response.get("input_hash"),
        "proposal": structured,
        "proposal_hash": sha256_json(structured),
        "usage": response.get("usage") or {},
        "raw_response_hash": response.get("raw_response_hash"),
        "raw_response_ref": response.get("raw_response_ref"),
    }
    if record["provider_input_hash"] and record["provider_input_hash"] != record["model_input_hash"]:
        raise ValueError("provider_input_hash_mismatch")
    record["generation_record_hash"] = sha256_json(record)
    return record
