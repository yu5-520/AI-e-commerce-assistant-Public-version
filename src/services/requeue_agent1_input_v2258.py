"""Sealed runtime CLI for V22.5.8 Agent1 evidence/output rejudgment."""
from __future__ import annotations

import argparse
import json

from src.services.agent1_input_recovery_v2258_service import requeue_agent1_v2258


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-version", default=None)
    parser.add_argument("--item-id", default=None)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = requeue_agent1_v2258(
        data_version=args.data_version,
        item_id=args.item_id,
        limit=args.limit,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
