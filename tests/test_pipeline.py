import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import Pipeline, parser
from gfe_reports import seconds, parse_log, database_series, delete_series, resolve_evidence


class MeasurementTests(unittest.TestCase):
    def test_exported_evidence_is_authoritative_after_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "original" / "logs" / "benchmark.log"
            old.parent.mkdir(parents=True)
            old.write_text("original evidence")
            output = root / "exported"
            retained = output / "logs" / old.name
            self.assertEqual(resolve_evidence(output, str(old)), retained)
            retained.parent.mkdir(parents=True)
            retained.write_text("retained evidence")
            self.assertEqual(resolve_evidence(output, str(old)).read_text(), "retained evidence")

    def test_duration_units_and_clock(self):
        self.assertAlmostEqual(seconds("250 ms"), .25)
        self.assertAlmostEqual(seconds("12 us"), .000012)
        self.assertEqual(seconds("01:02:03.5"), 3723.5)
        self.assertEqual(seconds("2:03 mins"), 123)
        self.assertEqual(seconds("2:03:04 hours"), 7384)
        self.assertEqual(seconds("2 days and 1:02:03 hours"), 176523)
        self.assertEqual(seconds("1 day(s) and 01:02:03 hours"), 90123)

    def test_analytics_summary_uses_microseconds_and_retains_timeouts(self):
        values = parse_log(">> BFS Execution time: 1 ms\n>> BFS N: 3, mean: 1234, median: 1234, num timeouts: 1]\n"
                           ">> BC N: 3, mean: 0, median: 0, num timeouts: 3]\n", "analytics")
        self.assertEqual(values["bfs_s"], .001234)
        self.assertEqual(values["bfs_timeouts"], 1)
        self.assertIsNone(values["bc_s"])

    def test_subsecond_updates_and_missing_deletion(self):
        values = parse_log("Loaded 100000 edges\nMemory consumption: 12MB\n"
                           "Insertions performed with 2 threads in 250 ms\n", "random")
        self.assertAlmostEqual(values["insertion_Mops_s"], .4)
        self.assertEqual(values["memory_MB"], 12)
        self.assertIsNone(values["deletion_Mops_s"])

    def test_analytics_means_repetitions_without_fabricating_missing(self):
        values = parse_log("Get 1-hop neighbors for 100 vertices performed with 2 threads in 5 ms\n"
                           ">> BFS Execution time: 10 ms\n>> BFS Execution time: 20 ms\n", "analytics")
        self.assertEqual(values["hop1_ops_s"], 20000)
        self.assertAlmostEqual(values["bfs_s"], .015)
        self.assertIsNone(values["bc_s"])

    def test_partial_mixed_progress_uses_expected_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript("CREATE TABLE aging (num_updates INTEGER, has_terminated_for_memfp INTEGER);"
                                         "INSERT INTO aging VALUES (1000,1);"
                                         "CREATE TABLE aging_intermediate_throughput3 (second INTEGER,num_operations INTEGER);"
                                         "INSERT INTO aging_intermediate_throughput3 VALUES (1,100),(2,200);")
            points, _, termination = database_series(path)
            self.assertEqual(points, [(10, 1), (20, 2)])
            self.assertEqual(termination, ["has_terminated_for_memfp"])

    def test_subsecond_aging_has_no_sample_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript("CREATE TABLE aging (num_updates INTEGER,completion_time INTEGER);"
                                         "INSERT INTO aging VALUES (1000,2000);")
            points, aging, termination = database_series(path)
            self.assertEqual(points, [])
            self.assertEqual(aging["completion_time"], 2000)
            self.assertEqual(termination, [])

    def test_delete_progress_uses_last_worker_start(self):
        points = delete_series("Progress: 50%\nDelete started.\nMemory consumption: 100 MB\n"
                               "Progress: 60%\nDelete started.\nProgress: 80%\nMemory consumption: 80 MB\n"
                               "[Aging2] Memory after: 70 MB.\n[Pipeline] Operations performed: 100 / 100\n")
        self.assertEqual(points, [(50, 80), (100, 70)])

    def test_incomplete_delete_does_not_stretch_final_sample(self):
        points = delete_series("Progress: 50%\nDelete started.\nProgress: 75%\n"
                               "[Aging2] Memory after: 70 MB.\n[Pipeline] Operations performed: 75 / 100\n")
        self.assertEqual(points, [(50, 70)])


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        args = parser().parse_args(["--profile", "smoke", "--run-dir", str(self.root / "output"),
                                   "--gfe-dir", str(self.root / "gfe"), "--data-dir", str(self.root / "data")])
        self.p = Pipeline(args)
        self.p.gfe.mkdir()
        self.p.datasets.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_timeout_keeps_log_and_durable_status(self):
        self.p.args.timeout_hours = .00002
        result = self.p.run("timeout", [sys.executable, "-u", "-c", "import time; print('partial output'); time.sleep(10)"], self.root)
        self.assertEqual(result["status"], "timeout")
        self.assertIn("partial output", Path(result["log"]).read_text())
        self.assertEqual(json.loads(self.p.manifest_path.read_text())["jobs"][-1]["exit_code"], 124)

    def test_missing_executable_is_recorded(self):
        result = self.p.run("missing", [self.root / "missing"], self.root)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 127)

    def test_runtime_writes_use_output_with_read_only_sources(self):
        optimizer = self.p.gfe / "optimizer"
        optimizer.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath('settings.txt').write_text('8')\n")
        optimizer.chmod(0o755)
        binary = self.p.gfe / "build" / "gfe_driver_radixgraph"
        binary.parent.mkdir()
        binary.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nassert Path('settings.txt').is_file()\nPath('driver-marker').write_text('ok')\n")
        binary.chmod(0o755)
        self.p.gfe.chmod(0o555)
        try:
            result = self.p.gfe_job("radixgraph", self.p.dataset_specs[0], "random")
            self.assertEqual(result["status"], "ok")
            self.assertEqual((self.p.gfe_work / "driver-marker").read_text(), "ok")
            self.assertFalse((self.p.gfe / "settings.txt").exists())
        finally:
            self.p.gfe.chmod(0o755)

    def test_concurrent_variant_and_explicit_boolean_values(self):
        spec = next(s for s in self.p.dataset_specs if s["name"] == "dota-league")
        with patch.object(self.p, "run", return_value={"status": "ok"}) as run:
            self.p.gfe_job("gtx", spec, "concurrent", variant="2hop-read-4",
                           metadata={"hop": 2, "read_threads": 4, "write_threads": 32})
        command = run.call_args.args[1]
        self.assertTrue(str(command[0]).endswith("gfe_driver_gtx_2hop"))
        for flag in ("--aging_memfp", "--aging_memfp_physical", "--aging_release_memory", "--mixed_workload"):
            self.assertIn(flag + "=true", command)
        self.assertEqual(command[command.index("-w") + 1], "32")

    def test_delete_false_flag_uses_cxxopts_equals_syntax(self):
        spec = next(s for s in self.p.dataset_specs if s["name"] == "graph500-24")
        with patch.object(self.p, "run", return_value={"status": "ok"}) as run:
            self.p.gfe_job("gtx", spec, "delete")
        command = run.call_args.args[1]
        self.assertIn("--aging_release_memory=false", command)
        self.assertIn("--delete_all=true", command)

    def test_twitter_expected_omission_paper_only(self):
        self.p.profile = "paper"
        spec = next(s for s in self.p.dataset_specs if s["name"] == "twitter-2010")
        result = self.p.gfe_job("teseo", spec, "random")
        self.assertEqual(result["status"], "expected_omission")


if __name__ == "__main__":
    unittest.main()
