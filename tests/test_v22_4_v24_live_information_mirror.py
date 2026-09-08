"""Live Java computation and committed Python snapshot integration, not replay evidence."""
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading
import time

import pytest

from src.services import canonical_product_snapshot_service as canonical
from src.services import v24_live_information_mirror_service as mirror

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = dict(sourceCommit="a" * 40, releaseHash="sha256:" + "b" * 64,
                javaContractHash="sha256:" + "c" * 64)


@pytest.fixture(scope="module")
def java_server(tmp_path_factory):
    java = ROOT / "runtime/java/jre/bin/java"
    jar = ROOT / "runtime/java/v24-production-authority.jar"
    if java.is_file() and jar.is_file():
        command = [str(java), "-cp", str(jar), "com.zcentury.v24.ProductionAuthorityMain"]
    else:
        home = os.environ.get("V24_TEST_JAVA_HOME") or os.environ.get("JAVA_HOME")
        if not home or not (Path(home) / "bin/javac").is_file():
            if os.environ.get("GITHUB_ACTIONS") == "true":
                pytest.fail("release gate requires the real packaged JRE")
            pytest.skip("build the Java bundle or set V24_TEST_JAVA_HOME to JDK 17+")
        classes = tmp_path_factory.mktemp("java-mirror-classes")
        sources = sorted((ROOT / "java-control-plane/src/main/java").rglob("*.java"))
        subprocess.run([str(Path(home) / "bin/javac"), "--release", "17", "-encoding", "UTF-8",
                        "-d", str(classes), *map(str, sources)], check=True, timeout=60)
        command = [str(Path(home) / "bin/java"), "-cp", str(classes),
                   "com.zcentury.v24.ProductionAuthorityMain"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, V24_AUTHORITY_HOST="127.0.0.1", V24_AUTHORITY_PORT=str(port),
               V24_AUTHORITY_MODE="READY_NO_AUTHORITY",
               V24_MIRROR_SOURCE_COMMIT=IDENTITY["sourceCommit"],
               V24_MIRROR_RELEASE_HASH=IDENTITY["releaseHash"],
               V24_MIRROR_CONTRACT_HASH=IDENTITY["javaContractHash"])
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert process.poll() is None
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail("Java readiness timeout")
        yield port
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("source,version", [
    ({"productId": "商品1", "storeId": "店铺1", "platform": "taobao", "paymentAmount": 123.5}, "DV1"),
    ({"productId": "2", "permissionStampId": "stamp", "roas": 2.0, "inventory": 0}, None),
    ({"productId": "3", "productMetricFacts": [{"factId": "f", "value": 12.25}]}, "DV2"),
])
def test_independent_live_java_hash_and_private_journal(java_server, tmp_path, source, version):
    collector = mirror.Collector(tmp_path, IDENTITY, java_server)
    result = canonical.build_canonical_product_snapshot_item(source, version)
    collector.submit(source, version, result["productSnapshotHash"])
    collector.pending.join()
    report = mirror.status(tmp_path, IDENTITY)
    assert report["counts"] == {"MATCH": 1}
    assert report["externalProductionMirrorParityProven"] is False
    assert report["cutoverAllowed"] is False
    records = [json.loads(line) for line in collector.path.read_text().splitlines()]
    assert all("input" not in r and "dataVersion" not in r for r in records)
    assert collector.path.stat().st_mode & 0o777 == 0o600


def test_mismatch_and_java_identity_rejection(java_server, tmp_path):
    collector = mirror.Collector(tmp_path, IDENTITY, java_server)
    collector.submit({"productId": "p"}, "DV", "sha256:" + "0" * 64)
    collector.pending.join()
    assert mirror.status(tmp_path, IDENTITY)["counts"] == {"MISMATCH": 1}
    wrong = dict(IDENTITY, sourceCommit="d" * 40)
    second = mirror.Collector(tmp_path / "other", wrong, java_server)
    second.submit({"productId": "p"}, "DV", "sha256:" + "0" * 64)
    second.pending.join()
    assert mirror.status(tmp_path / "other", wrong)["counts"] == {"SHADOW_RECEIPT_REJECTED": 1}


def test_java_never_accepts_python_result_or_authority_switch(java_server):
    connection = http.client.HTTPConnection("127.0.0.1", java_server, timeout=3)
    try:
        for body, expected in [
            ({"sampleId": "a" * 32, "input": {}, "dataVersion": None,
              "productionResultHash": "sha256:fake"}, 400),
            ({"sampleId": "a" * 32, "input": {"title": "x" * mirror.MAX_INPUT},
              "dataVersion": None}, 413),
        ]:
            connection.request("POST", "/v1/mirror/canonical-product", mirror.encoded(body))
            response = connection.getresponse()
            assert response.status == expected
            response.read()
        connection.request("GET", "/readyz")
        response = connection.getresponse()
        status = json.loads(response.read())
        assert status["ready"] is True
        assert status["authorityGeneration"] is None
        assert status["productionMutationAllowed"] is False
    finally:
        connection.close()


def test_unavailable_java_keeps_failure_visible(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        collector = mirror.Collector(tmp_path, IDENTITY, sock.getsockname()[1])
        collector.submit({"productId": "p"}, "DV", "sha256:" + "0" * 64)
        collector.pending.join()
    assert mirror.status(tmp_path, IDENTITY)["counts"] == {"SHADOW_UNAVAILABLE": 1}


def test_journal_tamper_and_pending_cannot_pass(tmp_path):
    collector = mirror.Collector(tmp_path, IDENTITY, 39024)
    collector.append(dict(sampleId="x", outcome="PENDING", inputWireHash=mirror.ZERO,
                          productionResultHash=mirror.ZERO))
    assert mirror.status(tmp_path, IDENTITY)["counts"] == {"PENDING": 1}
    assert mirror.status(tmp_path, IDENTITY)["status"] == "OBSERVATIONS_REQUIRE_REVIEW"
    collector.path.write_text(collector.path.read_text().replace('"PENDING"', '"MATCH"'))
    assert mirror.status(tmp_path, IDENTITY)["journalIntegrityVerified"] is False


def test_oversized_and_full_journal_are_visible(tmp_path, monkeypatch):
    collector = mirror.Collector(tmp_path, IDENTITY, 39024)
    collector.submit({"title": "x" * mirror.MAX_INPUT}, "DV", mirror.ZERO)
    assert mirror.status(tmp_path, IDENTITY)["counts"] == {"INPUT_TOO_LARGE": 1}
    monkeypatch.setattr(mirror, "MAX_JOURNAL", collector.path.stat().st_size)
    collector.submit({}, "DV", mirror.ZERO)
    assert mirror.status(tmp_path, IDENTITY)["journalIntegrityVerified"] is False


def test_queue_overflow_is_recorded_and_never_counted_as_match(tmp_path, monkeypatch):
    collector = mirror.Collector(tmp_path, IDENTITY, 39024)
    started, release = threading.Event(), threading.Event()

    def compare(seed, body):
        started.set()
        assert release.wait(5)
        return dict(seed, outcome="SHADOW_UNAVAILABLE")

    monkeypatch.setattr(collector, "compare", compare)
    try:
        collector.submit({}, None, mirror.ZERO)
        assert started.wait(2)
        for _ in range(33):
            collector.submit({}, None, mirror.ZERO)
        counts = mirror.status(tmp_path, IDENTITY)["counts"]
        assert counts == {"PENDING": 33, "QUEUE_FULL": 1}
    finally:
        release.set()
        collector.pending.join()


def test_production_hook_runs_after_commit_and_does_not_capture_pure_replay(tmp_path, monkeypatch):
    database = tmp_path / "business.sqlite"
    def connect():
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(canonical, "connect", connect)
    monkeypatch.setattr(canonical, "projected_products", lambda user_id: [{"productId": "p"}])
    observed = []

    def observe(pairs, version):
        with sqlite3.connect(database) as conn:
            assert conn.execute("SELECT COUNT(*) FROM canonical_product_snapshot_sets_v1").fetchone()[0] == 1
        observed.extend(pairs)

    monkeypatch.setattr(mirror, "observe_committed_products", observe)
    canonical.build_canonical_product_snapshot_item({"productId": "p"}, "DV")
    assert observed == []
    result = canonical.materialize_canonical_product_snapshot("DV")
    assert result["productCount"] == 1
    assert len(observed) == 1


def test_disabled_capture_and_empty_status(tmp_path, monkeypatch):
    monkeypatch.delenv("V24_LIVE_MIRROR_ENABLED", raising=False)
    mirror.observe_committed_products([({}, {})], None)
    report = mirror.status(tmp_path, IDENTITY)
    assert report["status"] == "NO_LIVE_SAMPLES"
    assert report["externalProductionMirrorParityProven"] is False
