from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import Pipeline, main, parser
from progress import ProgressBar
from gfe_reports import seconds, parse_log, database_series, delete_series, resolve_evidence, render_gfe_reports
from upstream_plots import METHODS, mixed_checkpoint_series


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

    def test_mixed_export_preserves_gtx_prefix_and_legacy_workload_progress(self):
        # A completed 200-operation input can represent either a new 20%
        # prefix or a historical full workload. Use recorded provenance rather
        # than inferring a restriction from the system name.
        for fraction, expected_progress, expected_checkpoints in (
                (.2, [10, 20], [3, 9]),
                (None, [50, 100], [3] * 5 + [9] * 5)):
            with self.subTest(fraction=fraction), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                (output / "raw").mkdir()
                (output / "logs").mkdir()
                database = output / "raw" / "mixed.sqlite3"
                with sqlite3.connect(database) as connection:
                    connection.executescript(
                        "CREATE TABLE aging (num_updates INTEGER,completion_time INTEGER);"
                        "INSERT INTO aging VALUES (200,9000000);"
                        "CREATE TABLE aging_intermediate_throughput3 "
                        "(second INTEGER,num_operations INTEGER);"
                        "INSERT INTO aging_intermediate_throughput3 VALUES (3,100);")
                log = output / "logs" / "mixed.log"
                log.write_text("[Pipeline] Operations performed: 200 / 200\n")
                metadata = dict(suite="gfe", system="gtx", dataset="graph500-24",
                                experiment="mixed", database=str(database))
                if fraction is not None:
                    metadata["workload_fraction"] = fraction
                record = dict(name="mixed-gtx-graph500-24", status="ok",
                              log=str(log), metadata=metadata)

                report = render_gfe_reports(output, [record])

                self.assertEqual(report["issues"], [])
                self.assertEqual(record["status"], "ok")
                with (output / "tables" / "figure9_mixed.csv").open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual([float(row["progress_pct"]) for row in rows], expected_progress)
                self.assertEqual([float(row["elapsed_s"]) for row in rows], [3, 9])
                series = mixed_checkpoint_series(rows)
                self.assertEqual(series["graph500-24"][list(METHODS).index("gtx")],
                                 expected_checkpoints)

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

    def enable_progress(self, names, interval=.01):
        self.progress_output = io.StringIO()
        self.p.progress_pending = set(names)
        self.p.progress = ProgressBar(len(names), stream=self.progress_output, interval=interval)

    def test_plan_respects_selected_stages_systems_and_datasets(self):
        self.p.args.systems = ["gtx"]
        self.p.args.datasets = ["com-lj.ungraph"]
        self.p.dataset_specs = self.p.dataset_specs[:1]
        self.p.args.skip_prepare = True
        self.assertEqual(self.p.planned_jobs({"figure8"}),
                         {"random-gtx-com-lj.ungraph", "vertices-gtx-com-lj.ungraph", "report"})
        self.assertEqual(self.p.planned_jobs({"figure9"}), {"report"})
        self.assertEqual(self.p.planned_jobs({"report"}), {"report"})
        self.p.args.skip_prepare = False
        self.assertEqual(self.p.planned_jobs({"prepare"}), {"prepare-data", "report"})

    def test_grouped_and_individual_stages_have_the_same_plan(self):
        grouped = self.p.planned_jobs({"main", "studies", "batch"})
        individual = self.p.planned_jobs({"figure8", "figure9", "figure10", "figure11",
                                          "figure12", "figure13", "tables", "batch"})
        self.assertEqual(grouped, individual)
        self.assertFalse(any(name.startswith("optimizer-") for name in grouped))

    def test_setup_does_not_advance_bar_and_omissions_count_once(self):
        self.enable_progress({"benchmark"})
        self.p.note("optimizer", "failed", "Setup failed")
        self.assertEqual(self.p.progress.completed, 0)
        self.p.note("benchmark", "expected_omission", "Omitted as in paper")
        self.p.complete_progress("benchmark", "expected_omission")
        self.assertEqual(self.p.progress.completed, 1)
        self.assertEqual(self.p.progress.failed, 0)

    def test_progress_polling_preserves_stdin_and_job_output(self):
        self.enable_progress({"stdin"})
        payload = "test input\n" * 10000
        result = self.p.run("stdin", [sys.executable, "-c",
                            "import sys, time; time.sleep(.04); data=sys.stdin.read(); "
                            "time.sleep(.04); sys.stdout.write(data)"], self.root, input_text=payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(Path(result["log"]).read_text(), payload)
        self.assertEqual(self.p.progress.completed, 1)
        self.assertGreater(len(self.progress_output.getvalue().splitlines()), 2)
        self.assertNotIn("\r", self.progress_output.getvalue())

    def test_interrupt_keeps_durable_status_without_counting_unfinished_job(self):
        self.enable_progress({"interrupt"})
        process = Mock()
        process.communicate.side_effect = KeyboardInterrupt
        with patch("pipeline.subprocess.Popen", return_value=process), patch.object(self.p, "stop") as stop:
            with self.assertRaises(KeyboardInterrupt):
                self.p.run("interrupt", ["benchmark"], self.root)
        stop.assert_called_once_with(process)
        saved = json.loads(self.p.manifest_path.read_text())["jobs"][-1]
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["exit_code"], 130)
        self.assertEqual(self.p.progress.completed, 0)

    def test_cli_report_progress_and_opt_out(self):
        for disabled in (False, True):
            args = ["run.sh", "--stages", "report", "--run-dir", str(self.p.output)]
            if disabled:
                args.append("--no-progress")
            with self.subTest(disabled=disabled), patch.object(sys, "argv", args):
                with patch("pipeline.export", return_value=[]), redirect_stderr(io.StringIO()) as progress:
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(main(), 0)
            if disabled:
                self.assertEqual(progress.getvalue(), "")
            else:
                self.assertIn("100% (1/1 jobs)", progress.getvalue())
                self.assertIn("finished", progress.getvalue())

    def test_cli_interrupt_retains_partial_bar_while_exporting(self):
        args = ["run.sh", "--profile", "smoke", "--stages", "figure8", "--skip-prepare",
                "--run-dir", str(self.root / "interrupted")]
        with patch.object(sys, "argv", args), patch("pipeline.Pipeline.run_main", side_effect=KeyboardInterrupt):
            with patch("pipeline.export", return_value=[]), redirect_stderr(io.StringIO()) as progress:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(), 130)
        self.assertIn("interrupted", progress.getvalue())
        self.assertNotIn("100%", progress.getvalue())

    def test_figure_stages_select_only_their_benchmarks(self):
        expected = {"figure8": {"random", "vertices"}, "figure9": {"mixed", "delete"},
                    "figure10": {"analytics"}, "figure11": {"concurrent"}}
        for stage, experiments in expected.items():
            with self.subTest(stage=stage), patch.object(self.p, "gfe_job") as job:
                self.p.run_main({stage})
                self.assertEqual({call.args[2] for call in job.call_args_list}, experiments)
                if stage == "figure9":
                    self.assertEqual({call.args[1]["name"] for call in job.call_args_list},
                                     {"graph500-24", "uniform-24"})

    def test_study_figure_stages_do_not_run_other_artifacts(self):
        from studies import run_studies
        for stage in ("figure12", "figure13"):
            with self.subTest(stage=stage), patch.object(self.p, "run", return_value={"status": "failed", "log": ""}) as run:
                run_studies(self.p, {stage})
                self.assertTrue(run.called)
                self.assertTrue(all(call.kwargs["metadata"]["artifact"] == stage
                                    for call in run.call_args_list))

    def test_unlimited_default_and_explicit_timeout_options(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(parser().parse_args([]).timeout_hours, 0)
        with patch.dict(os.environ, {"TIMEOUT_HOURS": "96"}):
            self.assertEqual(parser().parse_args([]).timeout_hours, 96)
            self.assertEqual(parser().parse_args(["--timeout-hours", "0"]).timeout_hours, 0)

    def test_unlimited_job_waits_without_deadline(self):
        self.p.args.timeout_hours = 0
        process = Mock(returncode=0)
        with patch("pipeline.subprocess.Popen", return_value=process):
            result = self.p.run("unlimited", ["benchmark"], self.root)
        self.assertEqual(result["status"], "ok")
        process.communicate.assert_called_once_with(input=None, timeout=None)

    def test_invalid_timeout_values_are_rejected(self):
        for value in ("-1", "nan", "inf"):
            with self.subTest(value=value):
                args = parser().parse_args(["--timeout-hours=" + value])
                with self.assertRaisesRegex(ValueError, "finite and nonnegative"):
                    Pipeline(args)

    def test_unlimited_gfe_jobs_disable_internal_limits(self):
        self.p.args.timeout_hours = 0
        spec = next(s for s in self.p.dataset_specs if s["name"] == "graph500-24")
        for experiment in ("random", "vertices", "analytics", "mixed", "delete", "concurrent"):
            with self.subTest(experiment=experiment):
                with patch.object(self.p, "run", return_value={"status": "ok"}) as run:
                    self.p.gfe_job("gtx", spec, experiment,
                                   metadata={"read_threads": 4, "write_threads": 32})
                command = run.call_args.args[1]
                self.assertEqual(command[command.index("--timeout") + 1], "0")
                if experiment in ("mixed", "delete", "concurrent"):
                    self.assertEqual(command[command.index("--aging_timeout") + 1], "0s")

    def test_explicit_limit_also_bounds_aging_workloads(self):
        self.p.args.timeout_hours = .25
        spec = next(s for s in self.p.dataset_specs if s["name"] == "graph500-24")
        with patch.object(self.p, "run", return_value={"status": "ok"}) as run:
            self.p.gfe_job("gtx", spec, "mixed")
        command = run.call_args.args[1]
        self.assertEqual(command[command.index("--aging_timeout") + 1], "900s")
        self.assertEqual(command[command.index("--timeout") + 1], "0")

    def test_timeout_keeps_log_and_durable_status(self):
        self.enable_progress({"timeout"})
        self.p.args.timeout_hours = .00002
        result = self.p.run("timeout", [sys.executable, "-u", "-c", "import time; print('partial output'); time.sleep(10)"], self.root)
        self.assertEqual(result["status"], "timeout")
        self.assertIn("partial output", Path(result["log"]).read_text())
        self.assertEqual(json.loads(self.p.manifest_path.read_text())["jobs"][-1]["exit_code"], 124)
        self.assertEqual(self.p.progress.completed, 1)
        self.assertEqual(self.p.progress.failed, 1)

    def test_missing_executable_is_recorded(self):
        self.enable_progress({"missing"})
        result = self.p.run("missing", [self.root / "missing"], self.root)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 127)
        self.assertEqual(self.p.progress.completed, 1)
        self.assertEqual(self.p.progress.failed, 1)

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

    def test_only_figure9_gtx_mixed_jobs_use_twenty_percent_graphlogs(self):
        for profile in ("paper", "smoke"):
            self.p.profile = profile
            for dataset in ("graph500-24", "uniform-24", "dota-league"):
                spec = next(s for s in self.p.dataset_specs if s["name"] == dataset)
                for system in ("teseo", "sortledton", "bvgt", "gtx", "radixgraph"):
                    for experiment in ("mixed", "delete", "concurrent"):
                        with self.subTest(profile=profile, dataset=dataset,
                                          system=system, experiment=experiment):
                            with patch.object(self.p, "optimize", return_value=True):
                                with patch.object(self.p, "run", return_value={"status": "ok"}) as run:
                                    self.p.gfe_job(system, spec, experiment,
                                                   metadata={"read_threads": 4, "write_threads": 32})
                            command = run.call_args.args[1]
                            metadata = run.call_args.kwargs["metadata"]
                            suffix = {"mixed": "-1.0", "delete": "-delete", "concurrent": ""}[experiment]
                            shortened = (system == "gtx" and experiment == "mixed"
                                         and dataset in ("graph500-24", "uniform-24"))
                            if shortened:
                                suffix += "-gtx-20"
                            expected = self.p.datasets / (dataset + suffix + ".graphlog")
                            self.assertEqual(command[command.index("--log") + 1], expected)
                            if experiment == "mixed":
                                self.assertEqual(metadata["graphlog"], str(expected))
                                self.assertEqual(metadata["workload_fraction"], .2 if shortened else 1)
                                if shortened:
                                    self.assertEqual(metadata["full_graphlog"],
                                                     str(self.p.datasets / (dataset + "-1.0.graphlog")))

    def test_snapshot_retains_both_full_and_gtx_prefix_graphlog_headers(self):
        spec = next(s for s in self.p.dataset_specs if s["name"] == "graph500-24")
        self.p.dataset_specs = [spec]
        headers = {"-1.0": "internal.edges.cardinality = 1000\n",
                   "-1.0-gtx-20": "internal.edges.cardinality = 200\n"}
        for suffix, header in headers.items():
            path = self.p.datasets / (spec["name"] + suffix + ".graphlog")
            path.write_bytes(header.encode() + b"__BINARY_SECTION_FOLLOWS\n\x00\xff")

        self.p.snapshot_inputs()

        entries = json.loads((self.p.output / "inputs.json").read_text())
        self.assertEqual(len(entries), 1)
        graphlogs = {Path(entry["path"]).name: entry for entry in entries[0]["graphlogs"]}
        self.assertEqual(len(graphlogs), 2)
        for suffix, header in headers.items():
            filename = spec["name"] + suffix + ".graphlog"
            self.assertEqual(graphlogs[filename]["header"], header)
            self.assertEqual(graphlogs[filename]["bytes"], (self.p.datasets / filename).stat().st_size)

    def test_twitter_omissions_apply_only_to_full_graph_paper_jobs(self):
        spec = next(s for s in self.p.dataset_specs if s["name"] == "twitter-2010")
        for profile in ("paper", "smoke"):
            self.p.profile = profile
            for system in ("teseo", "sortledton", "gtx"):
                for experiment in ("random", "vertices", "analytics"):
                    with self.subTest(profile=profile, system=system, experiment=experiment):
                        with patch.object(self.p, "run", return_value={"status": "ok"}) as run:
                            result = self.p.gfe_job(system, spec, experiment)
                        if profile == "paper" and experiment != "vertices":
                            self.assertEqual(result["status"], "expected_omission")
                            run.assert_not_called()
                        else:
                            self.assertEqual(result["status"], "ok")
                            run.assert_called_once()
                            command = run.call_args.args[1]
                            if experiment == "vertices":
                                self.assertEqual(command[command.index("-G") + 1], spec["vertices"])
                                self.assertIn("--insert_vertex_only=true", command)

    def test_twitter_spruce_and_radixgraph_jobs_remain_enabled(self):
        self.p.profile = "paper"
        spec = next(s for s in self.p.dataset_specs if s["name"] == "twitter-2010")
        for system in ("bvgt", "radixgraph"):
            for experiment in ("random", "vertices", "analytics"):
                with self.subTest(system=system, experiment=experiment):
                    with patch.object(self.p, "optimize", return_value=True):
                        with patch.object(self.p, "run", return_value={"status": "ok"}) as run:
                            result = self.p.gfe_job(system, spec, experiment)
                    self.assertEqual(result["status"], "ok")
                    run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
