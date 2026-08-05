from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.api.routes.modules.product_detail_v2256 import _aliases


ROOT = Path(__file__).resolve().parents[1]


def test_composite_product_aliases_cover_archive_and_raw_identity() -> None:
    item = {
        "id": "S001::P10001",
        "archiveId": "S001::P10001",
        "objectId": "JD::S001::P10001::SKU01",
        "productId": "P10001",
        "skuId": "SKU01",
        "productPosition": {"productId": "P10001", "skuId": "SKU01"},
    }
    aliases = _aliases(item)
    assert {"S001::P10001", "JD::S001::P10001::SKU01", "P10001", "SKU01"} <= aliases


def test_report_page_has_real_polling_and_observation_branch() -> None:
    report = (ROOT / "web_demo/modules/report/page.js").read_text(encoding="utf-8")
    assert 'fetch("/api/view/pipeline-live?limit=100"' in report
    assert 'cache: "no-store"' in report
    assert "schedulePoll(ctx, flowIsActive(latestLive) ? 2500 : 10000)" in report
    assert "visibilitychange" in report
    assert "observation-terminal" in report
    assert "商品信号" in report
    assert "观察沉淀" in report
    assert "NODE_CODE_BY_LABEL" in report


def test_product_page_restores_v217_geometry_and_rich_fact_layers() -> None:
    product = (ROOT / "web_demo/modules/product/page.js").read_text(encoding="utf-8")
    assert "/api/modules/product-detail-v2256/" in product
    assert "product-trend-summary" in product
    assert "product-snapshot-scroll" in product
    assert 'style="--snapshot-count:${snapshots.length}"' in product
    assert "最新一期经营事实" in product
    assert "流量来源" in product
    assert "指标事实" not in product
    assert "请先完成报表导入" not in product


def test_v2256_assets_are_single_version_and_loaded_last() -> None:
    index = (ROOT / "web_demo/index.html").read_text(encoding="utf-8")
    bootstrap = (ROOT / "web_demo/bootstrap.js").read_text(encoding="utf-8")
    assert "?v=22.5.0" not in index
    assert index.count("?v=22.5.6") >= 20
    assert 'const version = "22.5.6"' in index
    assert 'const ASSET_VERSION = "22.5.6"' in bootstrap
    assert index.index("v2256-report-product.css") > index.index("metro-line.css")


@pytest.mark.parametrize(
    "relative_path",
    [
        "web_demo/modules/report/page.js",
        "web_demo/modules/product/page.js",
        "web_demo/bootstrap.js",
    ],
)
def test_changed_javascript_syntax(relative_path: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    subprocess.run([node, "--check", str(ROOT / relative_path)], check=True, capture_output=True, text=True)
