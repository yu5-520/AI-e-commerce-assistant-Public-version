from __future__ import annotations

import sqlite3
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from src.services import repeatability_contract_v1_service as repeatability
from src.services import runtime_generation_barrier_v1_service as generation


@contextmanager
def _scope(conn: sqlite3.Connection):
    yield conn


class RuntimeGenerationBarrierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.connect_patch = patch.object(
            generation,
            "connect",
            side_effect=lambda: _scope(self.conn),
        )
        self.release_patch = patch.object(
            generation,
            "_release_identity_material",
            return_value={"sourceCommit": "TEST", "releaseHash": "sha256:release"},
        )
        self.connect_patch.start()
        self.release_patch.start()

    def tearDown(self) -> None:
        self.release_patch.stop()
        self.connect_patch.stop()
        self.conn.close()

    def test_reset_rotates_generation_and_finishes_empty(self) -> None:
        before = generation.ensure_runtime_generation_state()
        with generation.runtime_reset_barrier(reason="unit_reset", scope="demo") as transition:
            during = generation.current_runtime_generation()
            self.assertEqual(during["state"], "resetting")
            self.assertNotEqual(before["generationHash"], during["generationHash"])
            self.assertEqual(
                transition["previousGeneration"]["generationHash"],
                before["generationHash"],
            )
        after = generation.current_runtime_generation()
        self.assertEqual(after["state"], "empty")
        self.assertEqual(after["generationSeq"], before["generationSeq"] + 1)
        self.assertNotEqual(after["generationHash"], before["generationHash"])

    def test_reset_waits_for_complete_worker_iteration(self) -> None:
        generation.ensure_runtime_generation_state()
        worker_entered = threading.Event()
        worker_release = threading.Event()
        reset_entered = threading.Event()

        def worker() -> None:
            with generation.runtime_execution_guard("unit_worker"):
                worker_entered.set()
                worker_release.wait(timeout=2)

        def reset() -> None:
            with generation.runtime_reset_barrier(reason="unit_reset", scope="demo"):
                reset_entered.set()

        worker_thread = threading.Thread(target=worker)
        reset_thread = threading.Thread(target=reset)
        worker_thread.start()
        self.assertTrue(worker_entered.wait(timeout=1))
        reset_thread.start()
        time.sleep(0.05)
        self.assertFalse(reset_entered.is_set())
        worker_release.set()
        worker_thread.join(timeout=2)
        reset_thread.join(timeout=2)
        self.assertTrue(reset_entered.is_set())
        self.assertEqual(generation.current_runtime_generation()["state"], "empty")

    def test_task_set_hash_ignores_run_identity(self) -> None:
        left = [
            {
                "taskId": "TASK-A",
                "dataVersion": "DV-1",
                "executionHash": "sha256:one",
                "productId": "P1",
                "actionFamily": "ads",
                "owner": "operator",
                "executionTarget": "SKU-1",
                "createdAt": "2026-08-15T10:00:00",
            }
        ]
        right = [
            {
                "taskId": "TASK-B",
                "dataVersion": "DV-2",
                "executionHash": "sha256:two",
                "productId": "P1",
                "actionFamily": "ads",
                "owner": "operator",
                "executionTarget": "SKU-1",
                "createdAt": "2026-08-15T11:00:00",
            }
        ]
        left_hash = repeatability.task_set_semantic_hash(tasks=left)
        right_hash = repeatability.task_set_semantic_hash(tasks=right)
        self.assertEqual(left_hash["taskCount"], 1)
        self.assertEqual(
            left_hash["taskSetSemanticHash"],
            right_hash["taskSetSemanticHash"],
        )

    def test_task_set_hash_changes_when_business_action_changes(self) -> None:
        left = [{"productId": "P1", "actionFamily": "ads", "owner": "operator"}]
        right = [{"productId": "P1", "actionFamily": "price", "owner": "operator"}]
        self.assertNotEqual(
            repeatability.task_set_semantic_hash(tasks=left)["taskSetSemanticHash"],
            repeatability.task_set_semantic_hash(tasks=right)["taskSetSemanticHash"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
