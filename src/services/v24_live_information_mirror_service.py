"""Observe committed production snapshots without putting Java on the decision path.

Only hashes are persisted. A bounded background worker sends the source projection
to the packaged Java mapper; the Python result is never sent to Java. These are
INFORMATION observations, not four-domain generation/rollback parity evidence.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
from pathlib import Path
import queue
import re
import threading
import time
import uuid

LOG = logging.getLogger(__name__)
MAX_INPUT = 524288
MAX_JOURNAL = 16 * 1024 * 1024
ZERO = "sha256:" + "0" * 64
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
ROOT = Path(__file__).resolve().parents[2]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


class Collector:
    def __init__(self, directory: Path, identity: dict, port: int):
        self.directory = directory
        self.identity = identity
        self.port = port
        self.session = uuid.uuid4().hex
        self.path = directory / (self.session + ".jsonl")
        self.lock = threading.Lock()
        self.previous = ZERO
        self.sequence = 0
        self.pending = queue.Queue(maxsize=32)
        self.failed = False
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.worker = threading.Thread(target=self._run, name="v24-information-mirror", daemon=True)
        self.worker.start()

    def append(self, event):
        with self.lock:
            record = dict(event, **self.identity, schema="v24.live_information_observation.v1",
                          evidenceSource="LIVE_PRODUCTION_INFORMATION_OBSERVATION",
                          sessionId=self.session, sequence=self.sequence + 1,
                          previousHash=self.previous, observedAtNs=time.time_ns(),
                          domain="INFORMATION", productionOwner="PYTHON_PRODUCTION",
                          authorityTransferAllowed=False)
            record["recordHash"] = digest(encoded(record))
            body = encoded(record) + b"\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if os.fstat(fd).st_size + len(body) > MAX_JOURNAL:
                    raise ValueError("mirror_journal_full")
                with os.fdopen(fd, "ab", closefd=False) as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
            self.previous = record["recordHash"]
            self.sequence += 1

    def block(self, reason):
        self.failed = True
        # A separate marker makes journal-full/write failures visible to status.
        try:
            (self.directory / (self.session + ".blocked")).write_text(reason + "\n")
        except OSError:
            pass
        LOG.error("v24_live_mirror_blocked:%s", reason)

    def submit(self, source, data_version, product_hash):
        if self.failed:
            return
        sample = uuid.uuid4().hex
        try:
            body = encoded(dict(sampleId=sample, input=source, dataVersion=data_version))
            if not isinstance(product_hash, str) or not HASH.fullmatch(product_hash):
                raise ValueError("invalid_product_hash")
            seed = dict(sampleId=sample, inputWireHash=digest(body), productionResultHash=product_hash)
            self.append(dict(seed, outcome="PENDING"))
            if len(body) > MAX_INPUT:
                self.append(dict(seed, outcome="INPUT_TOO_LARGE"))
                return
            try:
                self.pending.put_nowait((seed, body))
            except queue.Full:
                self.append(dict(seed, outcome="QUEUE_FULL"))
        except Exception:
            self.block("capture_or_journal_failed")

    def compare(self, seed, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request("POST", "/v1/mirror/canonical-product", body,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(16385)
            if response.status != 200 or len(raw) > 16384:
                return dict(seed, outcome="SHADOW_HTTP_ERROR")
            result = json.loads(raw)
            expected = dict(self.identity, sampleId=seed["sampleId"],
                            inputWireHash=seed["inputWireHash"], domain="INFORMATION",
                            schema="v24.live_information_mirror.response.v1")
            if (any(result.get(key) != value for key, value in expected.items())
                    or result.get("productionMutationAllowed") is not False
                    or result.get("authorityGrantCreated") is not False
                    or not isinstance(result.get("shadowResultHash"), str)
                    or not HASH.fullmatch(result["shadowResultHash"])):
                return dict(seed, outcome="SHADOW_RECEIPT_REJECTED")
            shadow = result["shadowResultHash"]
            return dict(seed, shadowResultHash=shadow,
                        outcome="MATCH" if shadow == seed["productionResultHash"] else "MISMATCH")
        except (OSError, ValueError, http.client.HTTPException):
            return dict(seed, outcome="SHADOW_UNAVAILABLE")
        finally:
            connection.close()

    def _run(self):
        while True:
            seed, body = self.pending.get()
            try:
                self.append(self.compare(seed, body))
            except Exception:
                self.block("comparison_or_journal_failed")
            finally:
                self.pending.task_done()


_collector = None
_pid = None
_init_lock = threading.Lock()


def observe_committed_products(pairs, data_version):
    """Called only after the canonical production snapshot transaction commits."""
    if os.environ.get("V24_LIVE_MIRROR_ENABLED") != "1":
        return
    global _collector, _pid
    try:
        with _init_lock:
            if _collector is None or _pid != os.getpid():
                identity = {key: os.environ[env] for key, env in (
                    ("sourceCommit", "V24_MIRROR_SOURCE_COMMIT"),
                    ("releaseHash", "V24_MIRROR_RELEASE_HASH"),
                    ("javaContractHash", "V24_MIRROR_CONTRACT_HASH"))}
                if not re.fullmatch(r"[0-9a-f]{40}", identity["sourceCommit"]):
                    raise ValueError("invalid_source_commit")
                if not all(HASH.fullmatch(identity[key]) for key in ("releaseHash", "javaContractHash")):
                    raise ValueError("invalid_release_identity")
                directory = ROOT / "outputs/v24-live-mirror" / identity["sourceCommit"]
                _collector = Collector(directory, identity, int(os.environ["V24_AUTHORITY_PORT"]))
                _pid = os.getpid()
        for source, product in pairs:
            _collector.submit(source, data_version, product["productSnapshotHash"])
    except Exception:
        # Mirror failure must not change an already committed business result.
        LOG.exception("v24_live_mirror_capture_unavailable")


def status(directory: Path, identity: dict):
    counts = {}
    errors = []
    for path in sorted(directory.glob("*.jsonl")):
        previous = ZERO
        pending = {}
        try:
            if path.stat().st_size > MAX_JOURNAL:
                raise ValueError("journal_size_limit")
            for sequence, line in enumerate(path.read_bytes().splitlines(), 1):
                record = json.loads(line)
                record_hash = record.pop("recordHash")
                if (record_hash != digest(encoded(record)) or record["previousHash"] != previous
                        or record["sequence"] != sequence or record["sessionId"] != path.stem
                        or record["schema"] != "v24.live_information_observation.v1"
                        or record["evidenceSource"] != "LIVE_PRODUCTION_INFORMATION_OBSERVATION"
                        or record["domain"] != "INFORMATION"
                        or record["productionOwner"] != "PYTHON_PRODUCTION"
                        or record["authorityTransferAllowed"] is not False
                        or any(record[key] != value for key, value in identity.items())):
                    raise ValueError("journal_identity_or_chain_invalid")
                previous = record_hash
                sample = record["sampleId"]
                outcome = record["outcome"]
                if outcome == "PENDING":
                    if sample in pending:
                        raise ValueError("duplicate_sample")
                    pending[sample] = record
                else:
                    initial = pending.get(sample)
                    if initial is None or initial["outcome"] != "PENDING":
                        raise ValueError("terminal_without_pending")
                    if any(record[key] != initial[key] for key in ("inputWireHash", "productionResultHash")):
                        raise ValueError("terminal_input_changed")
                    if outcome in ("MATCH", "MISMATCH"):
                        if not HASH.fullmatch(record.get("shadowResultHash", "")):
                            raise ValueError("invalid_shadow_hash")
                        if (record["productionResultHash"] == record["shadowResultHash"]) != (outcome == "MATCH"):
                            raise ValueError("outcome_hash_disagrees")
                    elif outcome not in ("INPUT_TOO_LARGE", "QUEUE_FULL", "SHADOW_HTTP_ERROR",
                                         "SHADOW_RECEIPT_REJECTED", "SHADOW_UNAVAILABLE"):
                        raise ValueError("unknown_outcome")
                    pending[sample] = record
            for record in pending.values():
                outcome = record["outcome"]
                counts[outcome] = counts.get(outcome, 0) + 1
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(path.name + ":" + type(error).__name__)
    errors.extend(path.name for path in directory.glob("*.blocked"))
    return dict(identity, schema="v24.live_information_mirror.status.v1", counts=counts,
                journalIntegrityVerified=not errors, journalErrors=errors,
                status="NO_LIVE_SAMPLES" if not counts and not errors else
                       "OBSERVATIONS_REQUIRE_REVIEW" if errors or any(k != "MATCH" for k in counts)
                       else "INFORMATION_OBSERVATIONS_MATCH",
                coveredOperations=["INFORMATION:canonical-product"] if counts else [],
                missingDomains=["INVOCATION", "TEMPORAL", "MUTATION"],
                generationAndRollbackProofPresent=False,
                externalProductionMirrorParityProven=False, cutoverAllowed=False)


if __name__ == "__main__":
    manifest = json.loads((ROOT / "release/release-manifest.json").read_text())
    contract = json.loads((ROOT / "runtime/java/runtime-contract.json").read_text())
    identity = dict(sourceCommit=manifest["sourceCommit"], releaseHash=manifest["releaseHash"],
                    javaContractHash=contract["contractHash"])
    report = status(ROOT / "outputs/v24-live-mirror" / identity["sourceCommit"], identity)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report["status"] == "OBSERVATIONS_REQUIRE_REVIEW" else 0)
