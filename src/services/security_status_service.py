"""Deployment and HTTP status service."""

from __future__ import annotations

import os
from typing import Any, Dict

from src.core.context import UserContext
from src.db.base import orm_base_summary
from src.db.models import model_registry_summary
from src.db.session import database_runtime_summary
from src.middleware.api_rate_limit import api_rate_limit_summary
from src.middleware.security_headers import security_headers_summary
from src.services.llm_gateway_service import llm_gateway_control_summary
from src.services.repository_runtime_service import repository_runtime_summary
from src.services.tech_log_service import tech_log_summary
from src.services.worker_runtime_config_service import worker_runtime_summary

SECURITY_STATUS_VERSION = "5.4.0"


def security_status(ctx: UserContext) -> Dict[str, Any]:
    cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000")
    return {
        "version": SECURITY_STATUS_VERSION,
        "apiVersion": "5.4.0",
        "securityHeaders": security_headers_summary(),
        "apiRateLimit": api_rate_limit_summary(),
        "cors": {"allowOrigins": [item.strip() for item in cors_origins.split(",") if item.strip()]},
        "database": {"runtime": database_runtime_summary(), "ormBase": orm_base_summary(), "models": model_registry_summary(), "repositories": repository_runtime_summary(ctx), "alembic": {"enabled": True, "config": "alembic.ini", "versionsPath": "alembic/versions"}},
        "workerRuntime": worker_runtime_summary(),
        "llmGateway": llm_gateway_control_summary(ctx),
        "techLog": tech_log_summary(),
        "nginx": {"recommended": True, "configPath": "deploy/nginx/ai-erp.conf"},
        "deploymentMode": os.getenv("DEPLOYMENT_MODE", "demo"),
    }
