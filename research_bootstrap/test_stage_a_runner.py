import tempfile
import unittest
from pathlib import Path

from stage_a_runner import StageARunnerError, run_stage_a


class StageARunnerTest(unittest.TestCase):
    def test_dry_run_matches_p31_stage_a_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stage-a"
            first = run_stage_a(output_dir=out, dry_run=True)
            self.assertEqual(first["status"], "PASS")
            self.assertEqual(first["mode"], "DRY_RUN")
            self.assertEqual(first["model_generations"], 63)
            self.assertEqual(first["deterministic_runtime_replays"], 180)
            self.assertEqual(first["generation_store"]["events"], 63)
            self.assertEqual(first["runtime_store"]["events"], 180)
            self.assertEqual(first["resumed_generations"], 0)
            self.assertFalse(first["paid_model_calls_possible"])

            second = run_stage_a(output_dir=out, dry_run=True)
            self.assertEqual(second["model_generations"], 63)
            self.assertEqual(second["deterministic_runtime_replays"], 180)
            self.assertEqual(second["generation_store"]["events"], 63)
            self.assertEqual(second["runtime_store"]["events"], 180)
            self.assertEqual(second["resumed_generations"], 63)

    def test_paid_mode_is_fail_closed_without_external_gate_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(StageARunnerError):
                run_stage_a(output_dir=Path(tmp), dry_run=False)


if __name__ == "__main__":
    unittest.main()
