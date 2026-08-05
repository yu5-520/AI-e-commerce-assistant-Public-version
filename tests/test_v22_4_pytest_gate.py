from __future__ import annotations

import configparser
import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PATTERNS = (
    "test_v22_4_*.py",
    "test_v22_3_*.py",
    "test_v22_2_6_3_*.py",
    "test_v22_agent1_*.py",
)


def test_default_pytest_collection_is_bound_to_active_release_contracts() -> None:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini", encoding="utf-8")

    configured = tuple(
        line.strip()
        for line in parser["pytest"]["python_files"].splitlines()
        if line.strip()
    )
    assert configured == ACTIVE_PATTERNS
    assert parser["pytest"]["addopts"].strip() == "--strict-markers"


def test_active_release_gate_includes_each_current_runtime_layer() -> None:
    test_names = {path.name for path in (ROOT / "tests").glob("test_*.py")}
    selected = {
        name
        for name in test_names
        if any(fnmatch.fnmatch(name, pattern) for pattern in ACTIVE_PATTERNS)
    }

    required = {
        "test_v22_4_app_route_smoke.py",
        "test_v22_4_release_hash_seal.py",
        "test_v22_4_static_path_contract.py",
        "test_v22_4_pytest_gate.py",
        "test_v22_2_6_3_native_agent_runtime.py",
        "test_v22_agent1_observation_contract.py",
    }
    assert required <= selected
    assert "test_v2162_agent1_observation_contract.py" not in test_names


def test_legacy_contract_files_require_explicit_invocation() -> None:
    for retired_pattern in (
        "test_v19*.py",
        "test_v20*.py",
        "test_v21*.py",
        "test_v2162*.py",
        "test_v22_2_6_1*.py",
    ):
        assert retired_pattern not in ACTIVE_PATTERNS
