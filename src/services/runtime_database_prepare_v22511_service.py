"""V22.5.11 deterministic runtime database schema preparation.

Deployment must finish every release-owned SQLite schema change before it seals
``release-data-lineage.json``. FastAPI startup calls the same owner so a restart cannot
introduce a schema that deployment never attested.

V22.5.12 also makes this already-sealed ``src/**/*`` owner directly executable with
``python -m``. Deployment no longer depends on a separately allow-listed script.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Sequence

from scripts.sqlite_data_identity import database_identity
from src.repositories.artifact_repository import ensure_artifact_tables
from src.repositories.sqlite_repository import DB_PATH, init_db
from src.services.action_authority_v214_service import ensure_action_authority_tables
from src.services.agent2_runtime_resilience_v2143_service import (
    ensure_agent2_runtime_columns,
)
from src.services.agent_runtime_recovery_v2261_service import (
    ensure_agent1_runtime_columns,
)
from src.services.artifact_storage_service import ensure_artifact_storage
from src.services.frontend_view_artifact_v2259_service import (
    ensure_frontend_view_artifact_tables,
)
from src.services.hash_directed_artifact_runtime_v2259_service import (
    ensure_hash_directed_runtime_tables,
)
from src.services.llm_gateway_v196_service import ensure_llm_cache_table
from src.services.pipeline_agent3_sop_v225_service import (
    ensure_agent3_runtime_columns,
)
from src.services.pipeline_item_service import ensure_pipeline_item_tables
from src.services.task_detail_snapshot_v2024_service import (
    backfill_task_detail_snapshots,
)

RUNTIME_DATABASE_PREPARE_VERSION = "22.5.11"
RUNTIME_DATABASE_PREPARE_ENTRY_VERSION = "22.5.12"
RUNTIME_DATABASE_PREPARE_CONTRACT = "runtime_database_schema.prepare.v22511"
RUNTIME_DATABASE_PREPARE_MODULE = (
    "src.services.runtime_database_prepare_v22511_service"
)


def _identity() -> Dict[str, Any]:
    return database_identity(Path(DB_PATH), include_content_hash=False)


def _ensure_release_owned_schema_once() -> None:
    # Keep this list explicit. New release-owned tables or columns must be registered
    # here before application startup is allowed to depend on them.
    ensure_artifact_storage()
    init_db()
    ensure_artifact_tables()
    ensure_pipeline_item_tables()
    ensure_action_authority_tables()
    ensure_llm_cache_table()
    ensure_agent1_runtime_columns()
    ensure_agent2_runtime_columns()
    ensure_agent3_runtime_columns()
    ensure_hash_directed_runtime_tables()
    ensure_frontend_view_artifact_tables()
    # limit=0 materializes the read-model table and indexes without rewriting tasks.
    backfill_task_detail_snapshots(limit=0)


def prepare_runtime_database_schema(
    *,
    verify_idempotent: bool = False,
) -> Dict[str, Any]:
    """Materialize every release-owned SQLite table, column and index.

    When ``verify_idempotent`` is enabled, the exact schema preparation is run twice
    and the resulting schema Hash must remain unchanged. This is safe because this
    function contains schema owners only; business migrations and lease recovery are
    intentionally excluded.
    """
    before = _identity()
    _ensure_release_owned_schema_once()
    prepared = _identity()
    if prepared.get("verified") is not True:
        raise RuntimeError(f"runtime_database_schema_prepare_failed:{prepared}")

    second: Dict[str, Any] | None = None
    if verify_idempotent:
        _ensure_release_owned_schema_once()
        second = _identity()
        if second.get("verified") is not True:
            raise RuntimeError(f"runtime_database_schema_second_pass_failed:{second}")
        if prepared.get("schemaHash") != second.get("schemaHash"):
            raise RuntimeError(
                "runtime_database_schema_not_idempotent:"
                f"{prepared.get('schemaHash')}!={second.get('schemaHash')}"
            )

    return {
        "schema": RUNTIME_DATABASE_PREPARE_CONTRACT,
        "version": RUNTIME_DATABASE_PREPARE_VERSION,
        "entryVersion": RUNTIME_DATABASE_PREPARE_ENTRY_VERSION,
        "entryModule": RUNTIME_DATABASE_PREPARE_MODULE,
        "databasePath": prepared.get("databasePath"),
        "beforeSchemaHash": before.get("schemaHash"),
        "preparedSchemaHash": prepared.get("schemaHash"),
        "preparedStateHash": prepared.get("stateHash"),
        "quickCheck": prepared.get("quickCheck"),
        "verified": prepared.get("verified") is True,
        "idempotenceChecked": bool(verify_idempotent),
        "idempotent": (
            prepared.get("schemaHash") == second.get("schemaHash")
            if second is not None
            else None
        ),
        "secondPassSchemaHash": second.get("schemaHash") if second else None,
        "businessMigrationExecuted": False,
        "leaseRecoveryExecuted": False,
        "standaloneScriptRequired": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare and attest the release-owned SQLite schema before "
            "data-lineage sealing."
        )
    )
    parser.add_argument(
        "--verify-idempotent",
        action="store_true",
        help="Run the schema owner twice and require an unchanged schemaHash.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = prepare_runtime_database_schema(
        verify_idempotent=bool(args.verify_idempotent)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if result.get("verified") is not True:
        return 2
    if args.verify_idempotent and result.get("idempotent") is not True:
        return 3
    return 0


__all__ = [
    "RUNTIME_DATABASE_PREPARE_VERSION",
    "RUNTIME_DATABASE_PREPARE_ENTRY_VERSION",
    "RUNTIME_DATABASE_PREPARE_CONTRACT",
    "RUNTIME_DATABASE_PREPARE_MODULE",
    "prepare_runtime_database_schema",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
