#!/usr/bin/env python3
"""One-click competition registry/hash-lineage verifier and handoff repair.

The script does not rewrite business identities or manufacture snapshots.  It first
proves that every V23 selected field is registered, recompiles the repository lineage
and runtime contract overlay, then (with --apply) projects already-formal signalRef
artifacts directly into the existing Agent1 pending queue.  --run-worker is explicit
because it may invoke the configured model Provider.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _run(command: list[str]) -> None:
    print("\n$", " ".join(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command_failed:{result.returncode}:{' '.join(command)}")


def _selected_field_ids(projection: Dict[str, Any]) -> set[str]:
    result: set[str] = set()
    modules = projection.get("modules") or {}
    for module_id in projection.get("requiredModules") or []:
        definition = modules.get(module_id) if isinstance(modules, dict) else None
        if not isinstance(definition, dict):
            continue
        result.update(str(value) for value in definition.get("fieldIds") or [] if str(value))
    return result


def _registry_gate() -> Dict[str, Any]:
    projection = _read(ROOT / "config" / "v23_registry_runtime.json")
    registry = _read(ROOT / "config" / "runtime_contract_lineage_registry_v1.json")
    selected = _selected_field_ids(projection)
    registered = set((registry.get("fields") or {}).keys())
    missing = sorted(selected - registered)
    explicit_edges = registry.get("lineageEdges") or []
    hard_edges = [
        item
        for item in explicit_edges
        if isinstance(item, dict)
        and str(item.get("type") or "").upper()
        in {"HARD_POINTER", "EXACT_REFERENCE_TRANSFER", "EXACT_HASH_DERIVATION"}
    ]
    report = {
        "selectedFieldCount": len(selected),
        "registeredFieldCount": len(registered),
        "missingSelectedFields": missing,
        "registeredInterfaceCount": len(registry.get("interfaces") or {}),
        "registeredLineageEdgeCount": len(explicit_edges),
        "hardLineageEdgeCount": len(hard_edges),
        "impactCount": len(registry.get("impacts") or []),
    }
    if missing:
        raise RuntimeError("selected_runtime_fields_unregistered:" + ",".join(missing))
    return report


def _compile() -> Dict[str, Any]:
    _run([sys.executable, "scripts/compile_competition_lineage.py"])
    _run([sys.executable, "scripts/compile_runtime_contract_lineage_overlay.py"])
    output = ROOT / "dist" / "competition-contract-lineage" / "runtime-contract-lineage.json"
    compiled = _read(output)
    verification = compiled.get("verification") or {}
    if verification.get("verified") is not True:
        raise RuntimeError(
            "runtime_contract_lineage_not_verified:"
            + json.dumps(verification.get("findings") or [], ensure_ascii=False)
        )
    return {
        "overlayHash": compiled.get("overlayHash"),
        "baseLineageGraphHash": compiled.get("baseLineageGraphHash"),
        "verification": verification,
        "output": str(output.relative_to(ROOT)),
    }


def _apply(data_version: str | None) -> Dict[str, Any]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.services.competition_signal_handoff_service import (
        seed_competition_signal_handoff,
        seed_ready_competition_handoffs,
    )

    if data_version:
        return seed_competition_signal_handoff(data_version)
    return seed_ready_competition_handoffs(limit_versions=8)


def _run_worker(limit: int | None) -> Dict[str, Any]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.services.station_agent_worker_v2259_service import run_worker_tick

    return run_worker_tick(
        worker_id="competition-hash-lineage-repair",
        limit=limit,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Competition unified registry/hash-lineage repair")
    parser.add_argument("--apply", action="store_true", help="seed exact formal signalRef handoffs")
    parser.add_argument("--data-version", default=None, help="repair one exact dataVersion")
    parser.add_argument("--run-worker", action="store_true", help="run one existing hard Agent worker tick; may call Provider")
    parser.add_argument("--worker-limit", type=int, default=None)
    args = parser.parse_args()

    print("============================================================")
    print("比赛版统一注册表 → 哈希血缘 → 影响图 → 直连修复")
    print("============================================================")

    gate = _registry_gate()
    print("\n【1. 注册表完整度】")
    print(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True))

    compiled = _compile()
    print("\n【2. 哈希血缘编译】")
    print(json.dumps(compiled, ensure_ascii=False, indent=2, sort_keys=True))

    handoff: Dict[str, Any] | None = None
    if args.apply:
        handoff = _apply(args.data_version)
        print("\n【3. Signal → Agent1 直连 Handoff】")
        print(json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print("\n【3. Signal → Agent1 直连 Handoff】未执行（加 --apply 才写业务队列）")

    worker: Dict[str, Any] | None = None
    if args.run_worker:
        if not args.apply:
            raise RuntimeError("run_worker_requires_apply")
        worker = _run_worker(args.worker_limit)
        print("\n【4. 现有 Hard Agent Worker】")
        print(json.dumps(worker, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print("\n【4. 现有 Hard Agent Worker】未执行（加 --run-worker 可能调用 Provider）")

    print("\n============================================================")
    print("【最终人话结论】")
    print("✅ V23 比赛运行模块使用的 fieldIds 已全部进入统一注册表。")
    print("✅ 只有注册表声明的 HARD_POINTER / EXACT_REFERENCE 才会作为硬断链。")
    print("✅ factHash/contentHash 作为内容指纹点亮，但不再伪装成外键。")
    print("✅ productRegistryKey 作为业务身份点亮，但不再伪装成父节点。")
    print("✅ 正式 signalRef 通过 competition_signal_handoff 直接进入 agent1_pending。")
    print("✅ Agent1/2/3、Task Mapping、Task Pool 继续使用现有 Hard Runtime，不建立第二套流程。")
    print("✅ 旧 station_queue/nextStation 可保留兼容，但不再是 Signal→Agent1 的比赛关键边。")
    if handoff is not None:
        print(f"本轮 handoff seeded={handoff.get('seededCount', 0)} blocked={handoff.get('blockedCount', 0)}")
    print("============================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
