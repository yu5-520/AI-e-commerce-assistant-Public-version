#!/usr/bin/env python3
"""Replace the retired account-service dependency in action safety runtime."""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request


def api_request(url: str, token: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "action-authority-fixed-context-patcher",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def main() -> int:
    repo = os.environ["REPOSITORY"]
    branch = os.environ["TARGET_BRANCH"]
    token = os.environ["GH_TOKEN"]
    path = "src/services/action_authority_v21_service.py"
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    current = api_request(api + "?" + urllib.parse.urlencode({"ref": branch}), token)
    source = base64.b64decode(current["content"]).decode("utf-8")

    old_import = "from src.services.account_service import assignment_for_store, current_user, user_raw\n"
    new_import = '''from src.services.competition_operator_context_service import (
    COMPETITION_OPERATOR_ID,
    competition_operator,
)
'''
    if source.count(old_import) != 1:
        raise RuntimeError(f"expected one retired account import, found {source.count(old_import)}")
    source = source.replace(old_import, new_import, 1)

    anchor = 'AUTHORIZATION_DATA_MISSING = "authorization_data_missing"\n\n'
    helpers = '''AUTHORIZATION_DATA_MISSING = "authorization_data_missing"


def assignment_for_store(store_id: str | None) -> Dict[str, Any]:
    """Return the server-owned competition operator binding for action safety."""
    return {
        "storeId": store_id,
        "primaryOperatorId": COMPETITION_OPERATOR_ID,
        "source": "competition_fixed_operator_context",
    }


def current_user(_: str | None = None) -> Dict[str, Any]:
    """Compatibility view over the fixed runtime actor; no client identity is read."""
    return competition_operator()


def user_raw(_: str | None = None) -> Dict[str, Any]:
    return competition_operator()

'''
    if source.count(anchor) != 1:
        raise RuntimeError(f"authority constant anchor count={source.count(anchor)}")
    source = source.replace(anchor, helpers, 1)
    if "src.services.account_service" in source:
        raise RuntimeError("retired account-service import remains")
    compile(source, path, "exec")

    result = api_request(
        api,
        token,
        method="PUT",
        payload={
            "message": "fix: 动作安全服务改用固定运营上下文",
            "content": base64.b64encode(source.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": branch,
        },
    )
    print(result["commit"]["sha"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
