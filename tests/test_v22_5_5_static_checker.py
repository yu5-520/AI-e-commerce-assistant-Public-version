from __future__ import annotations

from scripts.check_v22_5_5_contract import main


def test_v22_5_5_static_contract_checker() -> None:
    assert main() == 0
