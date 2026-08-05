"""Prompt template service for LLM Gateway."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT_DIR / "prompts"

PROMPT_VERSION = "14.0.0"

DEFAULT_PROMPTS: Dict[str, str] = {
    "creative_test_package": """
你是电商标题主图测试包生成 Agent。请根据商品事实、类目 Profile、平台规则、竞品信号、RAG 经验和 ActionPlan，生成可上架小流量测试的标题 / 主图 / 卖点组合。
要求：只输出 JSON，不要 Markdown；不得建议越权动作或绕过复核。
""".strip(),
    "task_action_plan_enrich": """
你是电商经营任务说明增强 Agent。请基于确定性的 problemType 与 ActionPlan，把处理包写成更具体、可执行、可复核的任务说明。
要求：只输出 JSON；不得改变 problemType，不得新增越权动作。
""".strip(),
    "task_signal_agent_judgment": """
你是 V14 电商经营任务判断 Agent。你会收到一个 signal_pool 信号、RAG 上下文、系统提供的 fallbackDecision 和接口边界。
你的职责：结合指标事实、数据缺口、RAG经验、类目/公司基线、SOP经验和风险边界，判断该信号应该进入任务快照、总管复核、观察留痕，还是正常波动忽略。
必须只输出 JSON，字段必须包含：decision、confidence、reason、taskPlan、evidenceRequirements、reviewMetrics、riskBoundary。
decision 只能是 create_task_snapshot、manager_review_required、observe_only、ignore_noise 之一。
不要控制接口，不要创建任务，不要改变权限，不要改变生命周期状态；你只输出判断和任务草案。
历史数据不足、首份报表、不可比日期只能作为判断上下文，不能机械阻断补数任务、红线复核或人工确认任务。
""".strip(),
    "feedback_experience_card": """
你是电商运营经验卡提炼 Agent。请根据任务提交、总管复核、前后指标和日志，把经验整理成结构化经验卡草案。
要求：只输出 JSON；必须包含适用条件、不适用条件、结果指标和复核状态；不得自动批准入库。
""".strip(),
    "module_report_summary": """
你是经营详情报告摘要 Agent。请将模块数据、证据、ActionPlan 和 RAG 引用整理为运营可读摘要。
要求：只输出 JSON；摘要必须围绕问题类型、执行包、提交证据和复核标准。
""".strip(),
}


def prompt_path(prompt_name: str) -> Path:
    safe = prompt_name.replace("/", "_").replace("..", "_")
    return PROMPT_DIR / f"{safe}.md"


def load_prompt(prompt_name: str) -> str:
    path = prompt_path(prompt_name)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return DEFAULT_PROMPTS.get(prompt_name, DEFAULT_PROMPTS["module_report_summary"])


def render_prompt(prompt_name: str, variables: Dict[str, Any] | None = None) -> str:
    prompt = load_prompt(prompt_name)
    variables = variables or {}
    for key, value in variables.items():
        prompt = prompt.replace("{{" + key + "}}", str(value))
    return prompt


def prompt_summary() -> Dict[str, Any]:
    existing = []
    if PROMPT_DIR.exists():
        existing = sorted(path.stem for path in PROMPT_DIR.glob("*.md"))
    return {
        "version": PROMPT_VERSION,
        "promptDir": str(PROMPT_DIR.relative_to(ROOT_DIR)),
        "defaultPrompts": sorted(DEFAULT_PROMPTS.keys()),
        "filePrompts": existing,
        "rule": "V14 prompts allow Agent judgment inside station boundaries; interfaces, permissions and lifecycle remain deterministic.",
    }
