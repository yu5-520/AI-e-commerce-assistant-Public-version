from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_competition_qwen_live_evidence.py"
WORKFLOW = ROOT / ".github" / "workflows" / "competition-qwen-live-evidence.yml"
SAMPLES = [
    "AI经营参谋_脱敏样例_第1期.xlsx",
    "AI经营参谋_脱敏样例_第2期.xlsx",
    "AI经营参谋_脱敏样例_第3期.xlsx",
]


def test_qwen_live_evidence_uses_real_judge_xlsx_upload_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'SCHEMA = "competition.qwen_live_evidence.v1"' in text
    assert '"/api/data/upload/preview"' in text
    assert '"/api/data/upload/confirm"' in text
    assert '"aliyun_bailian"' in text
    assert '"agent3_sop_agent"' in text
    assert "competition_contract_fixture_provider.py" not in text
    for filename in SAMPLES:
        assert filename in text
        assert (ROOT / "web_demo" / "sample-data" / filename).is_file()


def test_qwen_live_workflow_never_loads_production_env_or_publishes_key():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.DASHSCOPE_API_KEY" in text
    assert "secrets.BAILIAN_API_KEY" in text
    assert "secrets.QWEN_API_KEY" in text
    assert "::add-mask::" in text
    assert "Production .env is intentionally not loaded" in text
    assert "/opt/ai-ecommerce-assistant/.env" not in text
    assert "qwen-live-attestation.json" in text
    assert "candidate-app.log" not in text
    assert "COMPETITION_BAILIAN_API_KEY=%s" in text


def test_qwen_live_attestation_has_explicit_production_disjoint_flags():
    text = SCRIPT.read_text(encoding="utf-8")
    for field in (
        "productionEnvironmentLoaded",
        "productionDatabaseReused",
        "productionServiceRestarted",
        "productionSymlinkSwitched",
    ):
        assert field in text
    assert '"published": False' in text
    assert '"sourceValuePersisted": False' in text
