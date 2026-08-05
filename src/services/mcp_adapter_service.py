"""MCP adapter placeholder for V4.5.

MCP is intentionally not the primary internal interface. The product uses:
- LLM Provider Gateway for model calls.
- Tool Gateway for internal safe tools.
- MCP Adapter later, only for external tool ecosystems.

This file documents and exposes the adapter boundary without opening live MCP
connections yet.
"""

from __future__ import annotations

from typing import Any, Dict

MCP_ADAPTER_VERSION = "4.5.0"


def mcp_adapter_summary() -> Dict[str, Any]:
    return {
        "version": MCP_ADAPTER_VERSION,
        "enabled": False,
        "position": "external_tool_adapter_only",
        "whyNotPrimary": [
            "当前核心问题是统一模型调用，不是开放外部工具协议。",
            "内部任务池、账号权限、ActionPlan 和审计链路不能被 MCP tools 绕过。",
            "真实店铺、ERP、CRM、投放、退款等动作暂不暴露为工具。",
        ],
        "futureSafeTools": [
            "search_rag_memory",
            "get_product_snapshot",
            "get_traffic_snapshot",
            "get_competitor_snapshot",
            "draft_task",
            "draft_experience_card",
        ],
        "blockedTools": [
            "change_price",
            "publish_product",
            "increase_ad_budget",
            "refund_order",
            "write_erp",
            "write_crm",
        ],
        "rule": "MCP 可以接外部数据和工具，但必须挂在 Tool Gateway 后面。",
    }


def list_mcp_servers() -> Dict[str, Any]:
    return {"version": MCP_ADAPTER_VERSION, "items": [], "message": "MCP servers are not connected in V4.5.0."}


def call_mcp_tool(server_id: str, tool_name: str, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "version": MCP_ADAPTER_VERSION,
        "ok": False,
        "serverId": server_id,
        "toolName": tool_name,
        "args": args or {},
        "message": "MCP tool execution is disabled in V4.5.0. Use internal Tool Gateway safe tools first.",
    }
