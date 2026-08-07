import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_competition_qwen_live_evidence.py"
RESOLVER = ROOT / "scripts" / "resolve_competition_qwen_credential.py"
WORKFLOW = ROOT / ".github" / "workflows" / "competition-qwen-live-evidence.yml"
SAMPLES = [
    "AI经营参谋_脱敏样例_第1期.xlsx",
    "AI经营参谋_脱敏样例_第2期.xlsx",
    "AI经营参谋_脱敏样例_第3期.xlsx",
]


def _load_resolver():
    spec = importlib.util.spec_from_file_location("competition_qwen_credential_resolver", RESOLVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_credential_resolver_mirrors_gateway_default_provider_semantics():
    resolver = _load_resolver()
    assert resolver.resolve({"LLM_API_KEY": "generic-qwen-key"}) == "generic-qwen-key"
    assert resolver.global_provider({"LLM_API_KEY": "generic-qwen-key"}) == "aliyun_bailian"
    assert resolver.resolve({"LLM_PROVIDER": "deepseek", "LLM_API_KEY": "generic"}) == ""
    assert resolver.resolve({"DEEPSEEK_API_KEY": "deep", "LLM_API_KEY": "generic"}) == ""
    assert resolver.resolve({"DASHSCOPE_API_KEY": "direct"}) == "direct"
    assert resolver.resolve({"PRODUCT_JUDGMENT_AGENT_API_KEY": "stage"}) == "stage"


def test_credential_resolver_reads_export_syntax_without_sourcing_file():
    resolver = _load_resolver()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "runtime.env"
        path.write_text("export LLM_API_KEY='masked-value'\n", encoding="utf-8")
        values = resolver.read_values("file", str(path))
        assert resolver.resolve(values) == "masked-value"
        diagnostic = resolver.safe_diagnostic(values)
        assert "LLM_API_KEY" in diagnostic
        assert "masked-value" not in diagnostic


def test_qwen_live_workflow_masks_credentials_and_never_sources_env_files():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.DASHSCOPE_API_KEY" in text
    assert "secrets.BAILIAN_API_KEY" in text
    assert "secrets.QWEN_API_KEY" in text
    assert "::add-mask::" in text
    assert "/etc/ai-ecommerce-assistant/qwen37-plus.env" in text
    assert "/opt/ai-ecommerce-assistant/shared/.env" in text
    assert "/root/apps/AI-e-commerce-assistant/.env" in text
    assert "resolve_competition_qwen_credential.py" in text
    assert "No credential value was printed" in text
    assert "source /etc/ai-ecommerce-assistant/qwen37-plus.env" not in text
    assert "source /opt/ai-ecommerce-assistant/shared/.env" not in text
    assert "source /root/apps/AI-e-commerce-assistant/.env" not in text
    assert ". /etc/ai-ecommerce-assistant/qwen37-plus.env" not in text
    assert ". /opt/ai-ecommerce-assistant/shared/.env" not in text
    assert ". /root/apps/AI-e-commerce-assistant/.env" not in text
    assert "qwen-live-attestation.json" in text
    assert "candidate-app.log" not in text
    assert "COMPETITION_BAILIAN_API_KEY=%s" in text
    assert "COMPETITION_BAILIAN_CREDENTIAL_SOURCE=%s" in text


def test_current_and_legacy_runtime_credential_fallbacks_are_read_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    resolver_text = RESOLVER.read_text(encoding="utf-8")
    assert 'pathlib.Path(f"/proc/{source}/environ")' in resolver_text
    assert "for runtime_root in /opt/ai-ecommerce-assistant /root/apps/AI-e-commerce-assistant" in text
    assert 'systemctl show "$service" --property=MainPID --value' in text
    assert "ecs_active_runtime_credential" in text
    assert "matching_app_process_credential" in text
    assert "uvicorn*src.api.main:app" in text
    for forbidden in (
        "systemctl stop",
        "systemctl restart",
        "systemctl start",
        "systemctl disable",
        "systemctl enable",
        "ln -sfn",
    ):
        assert forbidden not in text


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


def run_contract_checks() -> None:
    """Stdlib-only entrypoint for the pinned ECS tool Python."""
    test_qwen_live_evidence_uses_real_judge_xlsx_upload_contract()
    test_credential_resolver_mirrors_gateway_default_provider_semantics()
    test_credential_resolver_reads_export_syntax_without_sourcing_file()
    test_qwen_live_workflow_masks_credentials_and_never_sources_env_files()
    test_current_and_legacy_runtime_credential_fallbacks_are_read_only()
    test_qwen_live_attestation_has_explicit_production_disjoint_flags()


if __name__ == "__main__":
    run_contract_checks()
    print("COMPETITION_QWEN_LIVE_CONTRACT=verified")
