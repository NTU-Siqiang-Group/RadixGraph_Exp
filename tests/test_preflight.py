from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import main, parser
from preflight import GB, RequirementsError, allocated_cache_bytes, check_requirements


class RequirementsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.arguments = ["--data-dir", str(self.root / "data"), "--output-dir", str(self.root / "output")]
        self.args = parser().parse_args(self.arguments)
        self.resources = dict(cpu_threads=64, memory_bytes=256 * GB)
        self.probe = patch("preflight.detect_resources", return_value=self.resources).start()
        self.disk = patch("preflight.shutil.disk_usage", return_value=SimpleNamespace(free=512 * GB)).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.temporary.cleanup)

    def test_exact_minimum_passes_without_creating_paths(self):
        measured = check_requirements(self.args)
        self.assertEqual(measured["status"], "ok")
        self.assertFalse(self.args.data_dir.exists())
        self.assertFalse(self.args.output_dir.exists())

    def test_all_shortfalls_reported_together(self):
        self.resources.update(cpu_threads=8, memory_bytes=32 * GB)
        self.disk.return_value.free = 100 * GB
        with self.assertRaises(RequirementsError) as failure:
            check_requirements(self.args)
        message = str(failure.exception)
        for expected in ("8 usable", "32.0 GB", "100.0 GB", "64 required", "256 GB", "512 GB"):
            self.assertIn(expected, message)

    def test_existing_cache_counts_towards_storage_requirement(self):
        self.disk.return_value.free = 200 * GB
        with patch("preflight.allocated_cache_bytes", return_value=312 * GB):
            self.assertEqual(check_requirements(self.args)["status"], "ok")

    def test_output_space_does_not_inflate_dataset_storage(self):
        self.disk.side_effect = [SimpleNamespace(free=300 * GB), SimpleNamespace(free=800 * GB)]
        with self.assertRaisesRegex(RequirementsError, "Disk at"):
            check_requirements(self.args)

    def test_shared_filesystem_free_space_not_counted_twice(self):
        self.disk.return_value.free = 300 * GB
        with self.assertRaisesRegex(RequirementsError, "512 GB combined"):
            check_requirements(self.args)

    def test_smoke_and_report_only_skip_hardware_minimums(self):
        for selection in (["--profile", "smoke"], ["--stages", "report"]):
            args = parser().parse_args(self.arguments + selection)
            self.assertEqual(check_requirements(args)["status"], "skipped")
        self.probe.assert_not_called()
        self.disk.assert_not_called()

    def test_prepare_stage_is_checked(self):
        self.args.stages = ["prepare"]
        self.resources["cpu_threads"] = 4
        with self.assertRaisesRegex(RequirementsError, "CPU"):
            check_requirements(self.args)

    def test_unwritable_mount_error_identifies_directory(self):
        with patch("preflight.os.access", return_value=False):
            with self.assertRaisesRegex(RequirementsError, "bind mount ownership"):
                check_requirements(self.args)

    def test_cli_failure_starts_no_work_and_has_no_traceback(self):
        self.resources["cpu_threads"] = 4
        errors = io.StringIO()
        with patch.object(sys, "argv", ["run.sh", *self.arguments]), patch("pipeline.Pipeline") as pipeline:
            with redirect_stderr(errors):
                self.assertEqual(main(), 2)
        pipeline.assert_not_called()
        self.assertIn("ERROR: Minimum requirements not satisfied", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertFalse(self.args.output_dir.exists())

    def test_check_only_never_constructs_pipeline_or_downloads(self):
        with patch.object(sys, "argv", ["run.sh", *self.arguments, "--check-requirements"]):
            with patch("pipeline.Pipeline") as pipeline, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 0)
        pipeline.assert_not_called()
        self.assertIn("[requirements] OK", output.getvalue())
        self.assertFalse(self.args.output_dir.exists())

    def test_allocated_cache_ignores_hardlink_duplicates_and_symlink_targets(self):
        self.args.data_dir.mkdir()
        payload = self.args.data_dir / "payload"
        payload.write_bytes(b"x" * 8192)
        os.link(payload, self.args.data_dir / "hardlink")
        outside = self.root / "outside"
        outside.write_bytes(b"x" * 16384)
        (self.args.data_dir / "symlink").symlink_to(outside)
        expected = sum(path.stat().st_blocks * 512 for path in (self.args.data_dir, payload))
        self.assertEqual(allocated_cache_bytes(self.args.data_dir), expected)


if __name__ == "__main__":
    unittest.main()
