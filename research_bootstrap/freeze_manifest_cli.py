from __future__ import annotations

import argparse
import json
from pathlib import Path

from manifest_contract import freeze_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a concrete Reality Bias experiment manifest.")
    parser.add_argument("input", type=Path, help="Unfrozen manifest JSON")
    parser.add_argument("output", type=Path, help="Destination frozen manifest JSON")
    args = parser.parse_args()

    raw = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("manifest input must be a JSON object")
    frozen = freeze_manifest(raw, require_concrete_provider=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "FROZEN",
        "experiment_id": frozen["experiment_id"],
        "provider": frozen["provider"],
        "model_id": frozen["model_id"],
        "model_version": frozen["model_version"],
        "manifest_hash": frozen["manifest_hash"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
