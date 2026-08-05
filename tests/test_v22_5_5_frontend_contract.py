from __future__ import annotations

from pathlib import Path


def test_frontend_uses_public_list_composite_detail_and_stable_node_codes() -> None:
    root = Path(__file__).resolve().parents[1]
    api_client = (root / "web_demo/core/api-client.js").read_text(encoding="utf-8")
    product = (root / "web_demo/modules/product/page.js").read_text(encoding="utf-8")
    report = (root / "web_demo/modules/report/page.js").read_text(encoding="utf-8")
    index = (root / "web_demo/index.html").read_text(encoding="utf-8")

    assert 'const API_CLIENT_VERSION = "22.5.5"' in api_client
    assert 'productView: (params = {}) => optionalRequest(`/api/view/products' in api_client
    assert "clearApiCaches();" in api_client
    assert "AppApi.productView" in product
    assert "/api/modules/product-detail-v2256/" in product
    assert 'cache: "no-store"' in product
    assert "product-trend-summary" in product
    assert "--snapshot-count:${snapshots.length}" in product
    assert 'nodeCode: "agent1"' in report
    assert "NODE_CODE_BY_LABEL" in report
    assert "observation-terminal" in report
    assert "batchFailed" in report
    assert "requestPipelineLive" in report
    assert 'const version = "22.5.6"' in index
    assert "v2256-report-product.css?v=22.5.6" in index
