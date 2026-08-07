from pathlib import Path

from src.services.import_adapter_service import parse_upload_file


SAMPLE_DIR = Path(__file__).resolve().parents[1] / "web_demo" / "sample-data"
SAMPLES = [
    "AI经营参谋_脱敏样例_第1期.xlsx",
    "AI经营参谋_脱敏样例_第2期.xlsx",
    "AI经营参谋_脱敏样例_第3期.xlsx",
]
REQUIRED_SHEETS = {"商品经营明细", "店铺经营汇总", "流量来源明细"}


def test_competition_evaluator_xlsx_samples_are_real_parser_inputs():
    for filename in SAMPLES:
        path = SAMPLE_DIR / filename
        assert path.is_file(), f"missing evaluator sample: {filename}"

        parsed = parse_upload_file(
            filename,
            path.read_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        assert parsed["format"] == "xlsx"
        assert parsed["totalRows"] > 0
        assert parsed["sheetCount"] >= len(REQUIRED_SHEETS)
        assert REQUIRED_SHEETS.issubset(set(parsed["sheetRows"]))
        assert all(parsed["sheetRows"][sheet] for sheet in REQUIRED_SHEETS)


def test_competition_evaluator_entry_points_to_xlsx_samples():
    entry = (SAMPLE_DIR.parent / "competition-evaluator-entry.js").read_text(encoding="utf-8")
    for filename in SAMPLES:
        assert f"/web_demo/sample-data/{filename}" in entry
        assert f'filename: "{filename}"' in entry

    assert "评委样例 · XLSX" in entry
    assert ".xlsx,.xlsm,.xls,.csv,.json" in entry
