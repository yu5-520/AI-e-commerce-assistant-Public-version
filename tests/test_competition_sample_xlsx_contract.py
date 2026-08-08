import hashlib
import io

import pytest
from openpyxl import load_workbook

from src.repositories import sqlite_repository
from src.services.competition_sample_asset_service import (
    COMPETITION_SAMPLE_ASSET_TABLE,
    ensure_competition_sample_assets,
    get_competition_sample_asset,
    list_competition_sample_asset_metadata,
)
from src.services.competition_sample_report_service import (
    SAMPLE_HEADERS,
    SAMPLE_REPORTS,
    build_competition_sample_xlsx,
)
from src.services.import_adapter_service import parse_upload_file
from src.services.system_service import RUNTIME_TABLES

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def isolated_sample_db(monkeypatch, tmp_path):
    monkeypatch.setattr(sqlite_repository, "DB_PATH", tmp_path / "product_workbench.sqlite3")
    monkeypatch.setattr(sqlite_repository, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sqlite_repository, "_WAL_INITIALIZED", False)
    yield tmp_path / "product_workbench.sqlite3"


def test_seed_builder_is_byte_deterministic_and_valid_openxml():
    for period in (1, 2, 3):
        first = build_competition_sample_xlsx(period)
        second = build_competition_sample_xlsx(period)
        assert first == second
        assert first.startswith(b"PK")
        workbook = load_workbook(io.BytesIO(first), read_only=True, data_only=True)
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))
        assert list(rows[0]) == SAMPLE_HEADERS
        assert len(rows) == 4
        assert rows[1][0] == "COMP-P-CONVERSION"
        assert rows[2][0] == "COMP-P-OBSERVE"
        assert rows[3][0] == "COMP-P-SCALE"


def test_sqlite_is_runtime_authority_and_seed_is_idempotent(isolated_sample_db):
    first = ensure_competition_sample_assets()
    second = ensure_competition_sample_assets()

    assert first["assetCount"] == 3
    assert first["seededCount"] == 3
    assert second["assetCount"] == 3
    assert second["seededCount"] == 0
    assert first["contentSha256ByPeriod"] == second["contentSha256ByPeriod"]
    assert first["runtimeDownloadAuthority"] == "sqlite_blob"
    assert first["runtimeWorkbookGenerationAllowed"] is False

    metadata = list_competition_sample_asset_metadata()
    assert [item["period"] for item in metadata] == [1, 2, 3]
    assert all("content" not in item for item in metadata)

    for period in (1, 2, 3):
        asset = get_competition_sample_asset(period)
        payload = asset["content"]
        assert payload.startswith(b"PK")
        assert asset["byteSize"] == len(payload)
        assert asset["contentSha256"] == hashlib.sha256(payload).hexdigest()
        parsed = parse_upload_file(asset["filename"], payload, XLSX_MEDIA_TYPE)
        assert parsed["format"] == "xlsx"
        assert parsed["totalRows"] == 3


def test_database_blob_corruption_fails_closed(isolated_sample_db):
    ensure_competition_sample_assets()
    with sqlite_repository.connect() as conn:
        conn.execute(
            f"UPDATE {COMPETITION_SAMPLE_ASSET_TABLE} SET content_blob=? WHERE period=1 AND active=1",
            (b"PK-corrupted-database-asset",),
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="competition_sample_asset_hash_mismatch"):
        get_competition_sample_asset(1)


def test_periods_preserve_expected_business_trend():
    conversion_roi = [SAMPLE_REPORTS[p][0]["roi"] for p in (1, 2, 3)]
    scale_roi = [SAMPLE_REPORTS[p][2]["roi"] for p in (1, 2, 3)]
    assert conversion_roi == [3.2, 2.55, 2.08]
    assert scale_roi == [3.85, 4.25, 4.62]


def test_invalid_period_and_corrupt_upload_fail_closed():
    with pytest.raises(ValueError, match="competition_sample_period_not_found"):
        build_competition_sample_xlsx(4)
    with pytest.raises(ValueError, match="XLSX 文件损坏"):
        parse_upload_file("broken.xlsx", b"not-a-zip", XLSX_MEDIA_TYPE)


def test_sample_assets_are_not_deleted_by_demo_runtime_reset_contract():
    assert COMPETITION_SAMPLE_ASSET_TABLE not in RUNTIME_TABLES


def test_data_import_router_keeps_stable_sample_download_endpoint():
    from src.api.routes import data_import

    paths = {route.path for route in data_import.router.routes}
    assert "/api/data/sample-reports/{period}.xlsx" in paths
