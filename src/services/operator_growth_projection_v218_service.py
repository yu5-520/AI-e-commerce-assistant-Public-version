"""V21.8.0 operator growth projection.

The growth layer makes verified work visible to the employee without turning the
system into a game. Company position and system level stay separate. Experience
is derived from task lifecycle results; it never changes role, permission, pay or
assignment scope.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, List

from src.services.competition_operator_context_service import current_user

OPERATOR_GROWTH_PROJECTION_VERSION = "21.8.0"

_PROFILE_METADATA: Dict[str, Dict[str, str]] = {"competition_operator": {"displayName": "赛事运营工作台", "positionTitle": "运营", "employmentStartDate": "2026-08-01"}}


_LEVELS: List[Dict[str, Any]] = [
    {"level": 1, "name": "经营入门", "threshold": 0},
    {"level": 2, "name": "标准执行", "threshold": 120},
    {"level": 3, "name": "独立运营", "threshold": 320},
    {"level": 4, "name": "经营优化", "threshold": 680},
    {"level": 5, "name": "增长运营", "threshold": 1200},
    {"level": 6, "name": "经营统筹", "threshold": 1900},
    {"level": 7, "name": "经营专家", "threshold": 2800},
]

_DONE_STATUS = {
    "已完成",
    "已通过",
    "已确认",
    "已归档",
    "已写入复盘",
    "等待系统自动复盘",
    "completed",
    "reviewed",
    "archived",
    "learned",
}
_REVIEWED_STATUS = {"已通过", "已确认", "已归档", "已写入复盘", "reviewed", "archived", "learned"}
_LEARNED_STATUS = {"已归档", "已写入复盘", "archived", "learned"}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _task_status(task: Dict[str, Any]) -> str:
    return _text(task.get("workflowStatus") or task.get("status") or task.get("displayStatus"))


def _identity_values(user: Dict[str, Any], metadata: Dict[str, str]) -> set[str]:
    return {
        _text(user.get("id")),
        _text(user.get("name")),
        _text(metadata.get("displayName")),
    } - {""}


def _task_belongs_to_user(task: Dict[str, Any], user: Dict[str, Any], metadata: Dict[str, str]) -> bool:
    identities = _identity_values(user, metadata)
    direct = {
        _text(task.get("assigneeId")),
        _text(task.get("assigneeUserId")),
        _text(task.get("operatorId")),
        _text(task.get("ownerUserId")),
        _text(task.get("assigneeName")),
        _text(task.get("operatorName")),
    } - {""}
    if identities & direct:
        return True

    return False


def _tenure_days(start_date: str | None) -> int:
    try:
        started = datetime.strptime(_text(start_date), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 0
    return max(0, (date.today() - started).days + 1)


def _level_for_experience(experience: int) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
    current = _LEVELS[0]
    next_level: Dict[str, Any] | None = None
    for index, item in enumerate(_LEVELS):
        if experience >= int(item["threshold"]):
            current = item
            next_level = _LEVELS[index + 1] if index + 1 < len(_LEVELS) else None
        else:
            break
    return current, next_level


def build_operator_growth_projection(
    user_id: str | None,
    tasks: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    user = current_user(user_id)
    metadata = _PROFILE_METADATA.get(
        _text(user.get("id")),
        {
            "displayName": _text(user.get("name")) or "运营伙伴",
            "positionTitle": _text(user.get("roleName")) or "经营成员",
            "employmentStartDate": date.today().isoformat(),
        },
    )
    user_tasks = [
        task
        for task in (tasks or [])
        if isinstance(task, dict) and _task_belongs_to_user(task, user, metadata)
    ]
    completed = [task for task in user_tasks if _task_status(task) in _DONE_STATUS]
    reviewed = [task for task in user_tasks if _task_status(task) in _REVIEWED_STATUS]
    learned = [task for task in user_tasks if _task_status(task) in _LEARNED_STATUS]

    execution_experience = len(completed) * 20
    review_experience = len(reviewed) * 10
    learning_experience = len(learned) * 15
    experience = execution_experience + review_experience + learning_experience
    level, next_level = _level_for_experience(experience)
    level_start = int(level["threshold"])
    next_threshold = int(next_level["threshold"]) if next_level else experience
    denominator = max(1, next_threshold - level_start)
    progress = 100 if next_level is None else round((experience - level_start) / denominator * 100)

    return {
        "version": OPERATOR_GROWTH_PROJECTION_VERSION,
        "userId": user.get("id"),
        "accountName": user.get("name"),
        "displayName": metadata["displayName"],
        "positionTitle": metadata["positionTitle"],
        "roleId": user.get("roleId"),
        "roleName": user.get("roleName"),
        "employmentStartDate": metadata["employmentStartDate"],
        "tenureDays": _tenure_days(metadata["employmentStartDate"]),
        "completedTaskCount": len(completed),
        "reviewedTaskCount": len(reviewed),
        "learnedTaskCount": len(learned),
        "level": int(level["level"]),
        "levelName": level["name"],
        "experience": experience,
        "currentLevelExperience": max(0, experience - level_start),
        "nextLevelExperience": next_threshold,
        "experienceForNextLevel": max(0, next_threshold - experience),
        "progressPercent": max(0, min(100, progress)),
        "experienceBreakdown": {
            "verifiedExecution": execution_experience,
            "reviewValidation": review_experience,
            "knowledgeSettlement": learning_experience,
        },
        "experienceRule": "经验只来自已完成、已复核和已沉淀的真实任务；不改变岗位、权限、薪资或组织归属。",
        "publicRankingEnabled": False,
    }


__all__ = [
    "OPERATOR_GROWTH_PROJECTION_VERSION",
    "build_operator_growth_projection",
]
