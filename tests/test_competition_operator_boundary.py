from __future__ import annotations

import json
from pathlib import Path

from src.api.main import app, competition_runtime_boundary

ROOT = Path(__file__).resolve().parents[1]


def test_competition_routes_exclude_account_and_department_governance() -> None:
    paths = set((app.openapi().get("paths") or {}).keys())
    forbidden = {
        "/api/accounts",
        "/api/accounts/me",
        "/api/accounts/switch",
        "/api/approvals",
        "/api/action-authority",
        "/login",
        "/register",
    }
    assert not (paths & forbidden), sorted(paths & forbidden)
    assert "/api/competition/runtime-boundary" in paths


def test_fixed_operator_is_server_owned_and_not_a_login_system() -> None:
    value = competition_runtime_boundary()
    boundary = value["productBoundary"]
    actor = value["runtimeActor"]
    assert boundary["applicationLoginEnabled"] is False
    assert boundary["applicationAccountSystemEnabled"] is False
    assert boundary["roleSwitchEnabled"] is False
    assert boundary["tenantManagementEnabled"] is False
    assert boundary["clientIdentityOverrideAllowed"] is False
    assert actor == {
        "actorId": "competition_operator",
        "role": "operator",
        "workspaceId": "competition_demo",
        "serverInjected": True,
        "clientOverrideAllowed": False,
    }


def test_frontend_has_no_mock_identity_or_account_switching() -> None:
    combined = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "web_demo/index.html",
            "web_demo/bootstrap.js",
            "web_demo/core/api-client.js",
        )
    )
    for token in (
        "X-Mock-User-Id",
        "ai_ecommerce_v442_current_user_id",
        "/api/accounts",
        "switchAccount",
        "role-console",
        "admin123",
    ):
        assert token not in combined


def test_external_interfaces_are_default_deny_and_only_bailian_is_enabled() -> None:
    registry = json.loads((ROOT / "config/external_interface_registry.json").read_text(encoding="utf-8"))
    assert registry["defaultPolicy"] == "deny_unregistered_interface"
    enabled = [
        interface_id
        for interface_id, item in registry["interfaces"].items()
        if item["executionEnabled"] is True
    ]
    assert enabled == ["model.inference.aliyun_bailian"]
    for interface_id, item in registry["interfaces"].items():
        if interface_id != "model.inference.aliyun_bailian":
            assert item["bindingPresent"] is False
            assert item["executionEnabled"] is False
