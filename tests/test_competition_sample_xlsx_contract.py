import io

import pytest
from openpyxl import load_workbook

from src.services.competition_sample_report_service import (
    SAMPLE_HEADERS,
    SAMPLE_REPORTS,
    build_competition_sample_xlsx,
)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_all_three_evaluator_samples_are_real_openxml_xlsx():
    for period in (1, 2, 3):
        payload = build_competition_sample_xlsx(period)
        assert payload.startswith(b"PK")
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))
        assert list(rows[0]) == SAMPLE_HEADERS
        assert len(rows) == 4
        assert rows[1][0] == "COMP-P-CONVERSION"
        assert rows[2][0] == "COMP-P-OBSERVE"
        assert rows[3][0] == "COMP-P-SCALE"


def test_periods_preserve_expected_business_trend():
    conversion_roi = [SAMPLE_REPORTS[p][0]["roi"] for p in (1, 2, 3)]
    scale_roi = [SAMPLE_REPORTS[p][2]["roi"] for p in (1, 2, 3)]
    assert conversion_roi == [3.2, 2.55, 2.08]
    assert scale_roi == [3.85, 4.25, 4.62]


def test_invalid_period_fails_closed():
    with pytest.raises(ValueError, match="competition_sample_period_not_found"):
        build_competition_sample_xlsx(4)


def test_data_import_router_exposes_sample_endpoint_and_corrupt_xlsx_is_validation_error():
    from src.api.routes import data_import

    paths = {route.path for route in data_import.router.routes}
    assert "/api/data/sample-reports/{period}.xlsx" in paths
    with pytest.raises(ValueError, match="XLSX 文件损坏"):
        data_import.parse_upload_file("broken.xlsx", b"not-a-zip", content_type=XLSX_MEDIA_TYPE)
