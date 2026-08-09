#!/usr/bin/env python3
"""Resolve one Bailian/Qwen credential from an approved read-only source.

The resolver mirrors the legacy LLM gateway's provider-selection semantics closely
enough for competition evidence credential reuse:
- provider-specific Bailian/DashScope/Qwen keys always qualify;
- a generic LLM_API_KEY qualifies when the resolved global provider is Bailian;
- a stage-specific API key qualifies when that stage resolves to Bailian;
- absent an explicit provider and absent a DeepSeek key, the gateway default is
  aliyun_bailian.

Only the credential value is written to stdout for command substitution. The
caller MUST mask it before any later shell output. This script never writes the
credential to disk and never mutates the source. It is also the explicit credential
preflight used by the final competition Qwen evidence gate after deterministic
single-worker recovery has passed.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Dict, Iterable

PROVIDER_ALIASES = {
    "aliyun": "aliyun_bailian",
    "aliyun_bailian": "aliyun_bailian",
    "bailian": "aliyun_bailian",
    "dashscope": "aliyun_bailian",
    "qwen": "aliyun_bailian",
    "deepseek": "deepseek",
}
DEFAULT_PROVIDER = "aliyun_bailian"
DIRECT_BAILIAN_KEYS = ("BAILIAN_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY")
STAGES = (
    "PRODUCT_JUDGMENT_AGENT",
    "ACTION_PLAN_AGENT",
    "TASK_MAPPING_AGENT",
)


def normalize_provider(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    return PROVIDER_ALIASES.get(raw, raw or DEFAULT_PROVIDER)


def parse_records(records: Iterable[str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in records:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, candidate = line.split("=", 1)
        name = name.strip()
        value = candidate.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name] = value
    return values


def read_values(kind: str, source: str) -> Dict[str, str]:
    if kind == "file":
        path = pathlib.Path(source)
        if not path.is_file():
            return {}
        return parse_records(path.read_text(encoding="utf-8").splitlines())
    if kind == "proc":
        path = pathlib.Path(f"/proc/{source}/environ")
        if not path.is_file():
            return {}
        return parse_records(path.read_bytes().decode("utf-8", errors="ignore").split("\0"))
    raise ValueError(f"unsupported source kind: {kind}")


def global_provider(values: Dict[str, str]) -> str:
    explicit = values.get("LLM_PROVIDER")
    if explicit:
        return normalize_provider(explicit)
    if any(values.get(name) for name in DIRECT_BAILIAN_KEYS):
        return "aliyun_bailian"
    if values.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    return DEFAULT_PROVIDER


def resolve(values: Dict[str, str]) -> str:
    for name in DIRECT_BAILIAN_KEYS:
        if values.get(name):
            return values[name]

    provider = global_provider(values)
    if provider == "aliyun_bailian" and values.get("LLM_API_KEY"):
        return values["LLM_API_KEY"]

    for prefix in STAGES:
        stage_provider = normalize_provider(values.get(f"{prefix}_PROVIDER") or provider)
        stage_key = values.get(f"{prefix}_API_KEY")
        if stage_provider == "aliyun_bailian" and stage_key:
            return stage_key
    return ""


def safe_diagnostic(values: Dict[str, str]) -> str:
    key_names = sorted(
        name
        for name, value in values.items()
        if value and (name.endswith("_API_KEY") or name in DIRECT_BAILIAN_KEYS)
    )
    providers = {
        name: values[name]
        for name in sorted(values)
        if name.endswith("_PROVIDER") and values.get(name)
    }
    models = {
        name: values[name]
        for name in sorted(values)
        if name.endswith("_MODEL") and values.get(name)
    }
    return repr({
        "credentialVariableNames": key_names,
        "resolvedGlobalProvider": global_provider(values),
        "providerMarkers": providers,
        "modelMarkers": models,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("file", "proc"))
    parser.add_argument("source")
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()
    values = read_values(args.kind, args.source)
    if args.diagnostic:
        print(safe_diagnostic(values))
        return 0
    sys.stdout.write(resolve(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
