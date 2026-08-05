"""V23.2.17 sealed-runtime CLI for creating an independent Agent3 test task."""
from __future__ import annotations

import argparse
import json

from src.services.agent3_test_task_runner_v23217_service import (
    AGENT3_TEST_TASK_RUNNER_VERSION,
    rerun_agent3_as_test_task,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "复用原任务的Agent2完成Artifact，只重跑Agent3，并创建一个新的测试任务进入正式生命周期。"
        )
    )
    parser.add_argument(
        "--source-task-id",
        required=True,
        help="原生命周期任务ID，例如 LT-20260730102410-3C0372",
    )
    parser.add_argument(
        "--source-pipeline-item-id",
        required=True,
        help="原Pipeline Item ID，例如 PI-22249C7685E0368B",
    )
    parser.add_argument(
        "--purpose",
        default="verify_agent3_runtime",
        help="本次测试目的，会写入测试链路留痕",
    )
    parser.add_argument(
        "--created-by",
        default="agent3_test_runner",
        help="测试任务创建者标识",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = rerun_agent3_as_test_task(
            source_task_id=args.source_task_id,
            source_pipeline_item_id=args.source_pipeline_item_id,
            purpose=args.purpose,
            created_by=args.created_by,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "version": AGENT3_TEST_TASK_RUNNER_VERSION,
                    "ok": False,
                    "sourceTaskId": args.source_task_id,
                    "sourcePipelineItemId": args.source_pipeline_item_id,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                    "originalTaskReplacement": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
