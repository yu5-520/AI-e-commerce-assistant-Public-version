"""Data Import service for Mock ERP / CRM datasets.

This service turns examples/*.csv from passive demo files into product-level
import sources with validation results, import records, WorkflowRun records,
and node-level ExecutionLog records.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set
from uuid import uuid4

from src.repositories.sqlite_repository import insert_import_record, list_import_records as list_sqlite_import_records
from src.services.log_service import create_execution_log, create_workflow_run, finish_workflow_run

ROOT_DIR = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = ROOT_DIR / "examples"
LOG_DIR = ROOT_DIR / "logs"
IMPORT_LOG_PATH = LOG_DIR / "data_import_records.jsonl"

DATASET_CONFIGS: Dict[str, Dict[str, Any]] = {
    "products": {
        "label": "商品表",
        "filename": "mock_products.csv",
        "required_fields": [
            "product_id",
            "product_name",
            "category",
            "cost_price",
            "sale_price",
            "stock",
        ],
        "numeric_fields": ["cost_price", "sale_price", "activity_price", "shipping_cost", "stock"],
    },
    "orders": {
        "label": "订单表",
        "filename": "mock_orders.csv",
        "required_fields": ["order_id", "product_id", "order_time", "quantity", "actual_paid", "refund_status"],
        "numeric_fields": ["quantity", "order_amount", "actual_paid"],
    },
    "inventory": {
        "label": "库存表",
        "filename": "mock_inventory.csv",
        "required_fields": ["snapshot_id", "product_id", "current_stock", "available_stock", "safety_stock"],
        "numeric_fields": ["current_stock", "available_stock", "safety_stock"],
    },
    "refunds": {
        "label": "退款表",
        "filename": "mock_refunds.csv",
        "required_fields": ["refund_id", "order_id", "product_id", "refund_amount", "refund_reason"],
        "numeric_fields": ["refund_amount"],
    },
    "customers": {
        "label": "客户表",
        "filename": "mock_customers.csv",
        "required_fields": ["customer_id", "nickname_hash", "total_orders", "total_amount", "refund_count", "rfm_score"],
        "numeric_fields": ["total_orders", "total_amount", "refund_count"],
    },
    "customer_tags": {
        "label": "客户标签表",
        "filename": "mock_customer_tags.csv",
        "required_fields": ["tag_id", "customer_id", "tag_name", "tag_source", "confidence"],
        "numeric_fields": ["confidence"],
    },
    "interactions": {
        "label": "客户互动表",
        "filename": "mock_interactions.csv",
        "required_fields": ["interaction_id", "customer_id", "interaction_type", "channel", "content_summary", "sentiment"],
        "numeric_fields": [],
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(filename: str) -> List[Dict[str, str]]:
    path = EXAMPLES_DIR / filename
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def get_headers(filename: str) -> List[str]:
    path = EXAMPLES_DIR / filename
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        return next(reader, [])


def is_number(value: str) -> bool:
    if value is None or value == "":
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def list_import_sources() -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for dataset_name, config in DATASET_CONFIGS.items():
        filename = str(config["filename"])
        rows = read_csv(filename)
        headers = get_headers(filename)
        sources.append(
            {
                "dataset_name": dataset_name,
                "label": config["label"],
                "filename": filename,
                "path": f"examples/{filename}",
                "exists": (EXAMPLES_DIR / filename).exists(),
                "row_count": len(rows),
                "headers": headers,
                "required_fields": config["required_fields"],
            }
        )
    return sources


def validate_dataset(dataset_name: str) -> Dict[str, Any]:
    config = DATASET_CONFIGS[dataset_name]
    filename = str(config["filename"])
    rows = read_csv(filename)
    headers = get_headers(filename)
    required_fields = list(config["required_fields"])
    numeric_fields = list(config["numeric_fields"])

    missing_required_fields = [field for field in required_fields if field not in headers]
    empty_required_cells: List[Dict[str, Any]] = []
    invalid_number_cells: List[Dict[str, Any]] = []

    for index, row in enumerate(rows, start=2):
        for field in required_fields:
            if field in headers and row.get(field, "") == "":
                empty_required_cells.append({"row": index, "field": field})
        for field in numeric_fields:
            if field in headers and row.get(field, "") != "" and not is_number(row.get(field, "")):
                invalid_number_cells.append({"row": index, "field": field, "value": row.get(field)})

    status = "passed"
    if missing_required_fields or empty_required_cells or invalid_number_cells:
        status = "failed"
    elif not rows:
        status = "warning"

    return {
        "dataset_name": dataset_name,
        "label": config["label"],
        "filename": filename,
        "status": status,
        "row_count": len(rows),
        "headers": headers,
        "required_fields": required_fields,
        "missing_required_fields": missing_required_fields,
        "empty_required_cells": empty_required_cells[:20],
        "invalid_number_cells": invalid_number_cells[:20],
    }


def _ids(rows: List[Dict[str, str]], field: str) -> Set[str]:
    return {row.get(field, "") for row in rows if row.get(field, "")}


def validate_relationships() -> List[Dict[str, Any]]:
    products = read_csv("mock_products.csv")
    orders = read_csv("mock_orders.csv")
    inventory = read_csv("mock_inventory.csv")
    refunds = read_csv("mock_refunds.csv")
    customers = read_csv("mock_customers.csv")
    customer_tags = read_csv("mock_customer_tags.csv")
    interactions = read_csv("mock_interactions.csv")

    product_ids = _ids(products, "product_id")
    order_ids = _ids(orders, "order_id")
    customer_ids = _ids(customers, "customer_id")

    checks = [
        {
            "check_name": "orders_product_id_link",
            "status": "passed" if _ids(orders, "product_id").issubset(product_ids) else "failed",
            "missing_ids": sorted(_ids(orders, "product_id") - product_ids),
        },
        {
            "check_name": "inventory_product_id_link",
            "status": "passed" if _ids(inventory, "product_id").issubset(product_ids) else "failed",
            "missing_ids": sorted(_ids(inventory, "product_id") - product_ids),
        },
        {
            "check_name": "refunds_product_id_link",
            "status": "passed" if _ids(refunds, "product_id").issubset(product_ids) else "failed",
            "missing_ids": sorted(_ids(refunds, "product_id") - product_ids),
        },
        {
            "check_name": "refunds_order_id_link",
            "status": "passed" if _ids(refunds, "order_id").issubset(order_ids) else "failed",
            "missing_ids": sorted(_ids(refunds, "order_id") - order_ids),
        },
        {
            "check_name": "customer_tags_customer_id_link",
            "status": "passed" if _ids(customer_tags, "customer_id").issubset(customer_ids) else "failed",
            "missing_ids": sorted(_ids(customer_tags, "customer_id") - customer_ids),
        },
        {
            "check_name": "interactions_customer_id_link",
            "status": "passed" if _ids(interactions, "customer_id").issubset(customer_ids) else "failed",
            "missing_ids": sorted(_ids(interactions, "customer_id") - customer_ids),
        },
    ]
    return checks


def validate_all_imports(workflow_run_id: str | None = None) -> Dict[str, Any]:
    datasets = [validate_dataset(dataset_name) for dataset_name in DATASET_CONFIGS]
    relationship_checks = validate_relationships()
    failed_count = sum(1 for item in datasets if item["status"] == "failed") + sum(
        1 for item in relationship_checks if item["status"] == "failed"
    )
    warning_count = sum(1 for item in datasets if item["status"] == "warning")
    result = {
        "validation_run_id": f"VALIDATION_{uuid4().hex[:10]}",
        "validated_at": now_iso(),
        "status": "passed" if failed_count == 0 else "failed",
        "failed_count": failed_count,
        "warning_count": warning_count,
        "datasets": datasets,
        "relationship_checks": relationship_checks,
    }
    if workflow_run_id:
        create_execution_log(
            workflow_run_id=workflow_run_id,
            node_name="data_import_validation",
            status=result["status"],
            output_snapshot={
                "dataset_count": len(datasets),
                "failed_count": failed_count,
                "warning_count": warning_count,
            },
        )
    return result


def append_import_record(payload: Dict[str, Any]) -> None:
    IMPORT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with IMPORT_LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def import_mock_data() -> Dict[str, Any]:
    workflow_run = create_workflow_run(
        workflow_type="data_import_mock_csv",
        input_snapshot={"source": "examples/*.csv", "dataset_count": len(DATASET_CONFIGS)},
    )
    workflow_run_id = workflow_run["workflow_run_id"]
    try:
        validation = validate_all_imports(workflow_run_id=workflow_run_id)
        record = {
            "import_id": f"IMPORT_{uuid4().hex[:10]}",
            "workflow_run_id": workflow_run_id,
            "created_at": now_iso(),
            "mode": "mock_csv",
            "status": validation["status"],
            "dataset_count": len(DATASET_CONFIGS),
            "total_rows": sum(item["row_count"] for item in validation["datasets"]),
            "validation": validation,
        }
        append_import_record(record)
        insert_import_record(record)
        create_execution_log(
            workflow_run_id=workflow_run_id,
            node_name="data_import_record",
            status=record["status"],
            output_snapshot={
                "import_id": record["import_id"],
                "total_rows": record["total_rows"],
            },
        )
        finish_workflow_run(
            workflow_run_id=workflow_run_id,
            workflow_type="data_import_mock_csv",
            status=record["status"],
            output_snapshot={
                "import_id": record["import_id"],
                "total_rows": record["total_rows"],
                "failed_count": validation["failed_count"],
            },
        )
        return record
    except Exception as exc:
        create_execution_log(
            workflow_run_id=workflow_run_id,
            node_name="data_import_error",
            status="failed",
            error_message=str(exc),
        )
        finish_workflow_run(
            workflow_run_id=workflow_run_id,
            workflow_type="data_import_mock_csv",
            status="failed",
            error_message=str(exc),
        )
        raise


def list_import_records(limit: int = 20) -> List[Dict[str, Any]]:
    sqlite_records = list_sqlite_import_records(limit=limit)
    if sqlite_records:
        return sqlite_records
    if not IMPORT_LOG_PATH.exists():
        return []
    lines = IMPORT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return records[-limit:][::-1]
