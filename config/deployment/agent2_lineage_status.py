"""Read-only Agent2 diagnosis through registered Artifact parent/child edges."""
import argparse
import json
from pathlib import Path
import sqlite3


def inspect(conn, item_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM pipeline_items WHERE item_id=?", (item_id,)).fetchone()
    if row is None:
        raise ValueError("未找到任务")
    item = dict(row)
    refs = json.loads(item.get("artifact_refs_json") or "{}")
    original = refs.get("agent2DraftInputRef")
    bound = refs.get("agent2ExecutionInputRef")
    roots = [x for x in (original, bound) if x]
    visited = set(roots)
    frontier = roots
    for _ in range(8):
        children = []
        for parent in frontier:
            children.extend(r[0] for r in conn.execute(
                "SELECT child_artifact_id FROM artifact_edges WHERE parent_artifact_id=? LIMIT 257", (parent,)))
        frontier = sorted(set(children) - visited)
        visited.update(frontier)
        if len(visited) > 256:
            raise ValueError("血缘节点超过256，请按输入引用进一步缩小范围")
        if not frontier:
            break
    executions = []
    for ref in sorted(visited):
        executions.extend(dict(r) for r in conn.execute(
            "SELECT execution_hash,input_artifact_ref,input_content_hash,status,attempt_count,"
            "accepted_output_ref,last_error,updated_at FROM artifact_execution_index_v2259 "
            "WHERE input_artifact_ref=? ORDER BY updated_at DESC LIMIT 10", (ref,)))
    payload = json.loads(item.get("payload") or "{}")
    if isinstance(payload.get("payload"), dict):
        payload = {**payload, **payload["payload"]}
    provider = payload.get("agent2Provider") or {}
    return {"任务": item_id, "阶段": item.get("current_stage"),
            "数据批次": item.get("data_version"), "重试次数": item.get("retry_count"),
            "错误码": item.get("last_error_code") or item.get("failure_code"),
            "失败原因": item.get("error_reason"), "原始输入": original,
            "当前执行输入": bound, "血缘节点数": len(visited),
            "调用错误": provider.get("errors", []), "逐项失败": provider.get("itemFailures", {}),
            "执行记录": executions, "查询模式": "只读，不重试、不修改验收状态"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("item_id")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    with sqlite3.connect((root / "logs/product_workbench.sqlite3").as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        print(json.dumps(inspect(conn, args.item_id), ensure_ascii=False, indent=2))
