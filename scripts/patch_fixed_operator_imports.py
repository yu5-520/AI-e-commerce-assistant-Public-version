#!/usr/bin/env python3
"""Move runtime consumers from the removed account service to fixed context."""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request

FILES = [
    "src/api/routes/audit.py",
    "src/api/routes/data_import.py",
    "src/api/routes/modules/aftersales.py",
    "src/api/routes/modules/dashboard.py",
    "src/api/routes/modules/feedback_flywheel.py",
    "src/api/routes/modules/inventory.py",
    "src/api/routes/modules/listing.py",
    "src/api/routes/modules/neural_operating.py",
    "src/api/routes/modules/operating_unit.py",
    "src/api/routes/modules/product.py",
    "src/api/routes/modules/product_detail_v2256.py",
    "src/api/routes/modules/rag_memory.py",
    "src/api/routes/modules/report_v5.py",
    "src/api/routes/modules/todo.py",
    "src/api/routes/modules/traffic.py",
    "src/api/routes/ops.py",
    "src/api/routes/stations.py",
    "src/api/routes/system.py",
    "src/api/routes/task_lifecycle_stations.py",
    "src/api/routes/task_pool.py",
    "src/api/routes/task_snapshots.py",
    "src/services/dashboard_service.py",
    "src/services/data_version_service.py",
    "src/services/experience_memory_service.py",
    "src/services/feedback_flywheel_service.py",
    "src/services/module_task_service.py",
    "src/services/neural_operating_read_model_v218_service.py",
    "src/services/operating_object_store_service.py",
    "src/services/operating_unit_snapshot_service.py",
    "src/services/operator_growth_projection_v218_service.py",
    "src/services/task_acceptance_assignment_station_service.py",
    "src/services/task_evidence_service.py",
    "src/services/task_lifecycle_state_machine_service.py",
    "src/services/task_pool_station_service.py",
]

OLD_MODULE = "src.services.account_service"
NEW_MODULE = "src.services.competition_operator_context_service"


def request(url: str, token: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fixed-operator-import-migrator",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)


def transform(path: str, source: str) -> str:
    count = source.count(OLD_MODULE)
    if count != 1:
        raise RuntimeError(f"{path}: expected one retired module import, found {count}")
    source = source.replace(OLD_MODULE, NEW_MODULE, 1)
    if path == "src/services/task_acceptance_assignment_station_service.py":
        source = source.replace(
            'reviewer_id = reviewer_id or (default_reviewer() or {}).get("id") or "U002"',
            'reviewer_id = reviewer_id or (default_reviewer() or {}).get("id")',
            1,
        )
    if OLD_MODULE in source:
        raise RuntimeError(f"{path}: retired account module remains")
    compile(source, path, "exec")
    return source


def main() -> int:
    repo = os.environ["REPOSITORY"]
    branch = os.environ["TARGET_BRANCH"]
    token = os.environ["GH_TOKEN"]
    commits: list[dict[str, str]] = []
    for path in FILES:
        api = f"https://api.github.com/repos/{repo}/contents/{path}"
        current = request(api + "?" + urllib.parse.urlencode({"ref": branch}), token)
        source = base64.b64decode(current["content"]).decode("utf-8")
        updated = transform(path, source)
        result = request(
            api,
            token,
            method="PUT",
            payload={
                "message": f"fix: {path} 改用固定运营上下文",
                "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
                "sha": current["sha"],
                "branch": branch,
            },
        )
        commits.append({"path": path, "commit": result["commit"]["sha"]})
    print(json.dumps({"updated": commits, "count": len(commits)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
