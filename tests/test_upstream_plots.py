import csv
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from upstream_plots import _stage_raw_gfe, run_original, write_plot_csv
from gfe_reports import DATASETS, LABELS, METHODS


class UpstreamPlotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_csv_preserves_original_method_dataset_order_and_empty_column(self):
        records = [dict(system=system, dataset=dataset, experiment="random",
                        status="ok", insertion_Mops_s=100 * method + graph + .125)
                   for method, system in enumerate(METHODS)
                   for graph, dataset in enumerate(DATASETS)]
        path = self.root / "csv" / "edge_insertion.csv"
        write_plot_csv(path, list(reversed(records)), "random", "insertion_Mops_s")
        with path.open(newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["Method", *LABELS, ""])
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(len(row) == 8 and row[-1] == "" for row in rows))
        self.assertEqual([row[0] for row in rows[1:]], list(METHODS.values()))
        for method, row in enumerate(rows[1:]):
            self.assertEqual([float(value) for value in row[1:-1]],
                             [100 * method + graph + .125 for graph in range(6)])

    def test_csv_retains_missing_failed_and_nonfinite_measurements_as_nan(self):
        records = [dict(system="radixgraph", dataset=dataset, experiment="analytics",
                        status=status, bfs_s=value)
                   for dataset, status, value in zip(DATASETS,
                       ["ok", "failed", "ok", "ok", "ok", "ok"],
                       ["0.00123456789", 10, None, float("nan"), float("inf"), "invalid"])]
        # A different experiment must not supply a missing analytics value.
        records.append(dict(system="gtx", dataset=DATASETS[0], experiment="random",
                            status="ok", bfs_s=99))
        path = self.root / "bfs_latency.csv"
        write_plot_csv(path, records, "analytics", "bfs_s", scale=1000)
        with path.open(newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertAlmostEqual(float(rows[5][1]), 1.23456789)
        self.assertEqual(rows[5][2:-1], ["NaN"] * 5)
        self.assertTrue(all(row[1:-1] == ["NaN"] * 6 for row in rows[1:5]))

    def test_raw_staging_omits_missing_or_short_sample_databases(self):
        output, work = self.root / "output", self.root / "plotting"
        raw = output / "raw"
        raw.mkdir(parents=True)
        records = []
        cases = [("absent", None), ("no-table", None), ("empty", 0), ("one", 1), ("valid", 2)]
        for name, count in cases:
            source = raw / (name + ".sqlite3")
            if name != "absent":
                with sqlite3.connect(source) as connection:
                    connection.execute("CREATE TABLE provenance (value TEXT)")
                    connection.execute("INSERT INTO provenance VALUES ('retained original')")
                    if count is not None:
                        connection.execute("CREATE TABLE aging_intermediate_throughput3 "
                                           "(id INTEGER, run INTEGER, second INTEGER, num_operations INTEGER)")
                        connection.executemany("INSERT INTO aging_intermediate_throughput3 VALUES (?,?,?,?)",
                                               [(i, 7, i + 1, 100 * (i + 1)) for i in range(count)])
            records.append(dict(name=name, status="ok", metadata=dict(
                suite="gfe", system="radixgraph", dataset=name, experiment="mixed",
                database="raw/" + source.name)))
        valid_bytes = (raw / "valid.sqlite3").read_bytes()
        omissions = _stage_raw_gfe(output, records, work)
        self.assertEqual({entry["job"] for entry in omissions}, {"absent", "no-table", "empty", "one"})
        staged = work / "results" / "radixgraph" / "mixed"
        self.assertEqual([path.name for path in staged.iterdir()], ["valid.sqlite3"])
        self.assertEqual((staged / "valid.sqlite3").read_bytes(), valid_bytes)
        self.assertFalse((raw / "absent.sqlite3").exists())
        for system in METHODS:
            for folder in ("mixed", "delete-only", "concurrent-1-hop", "concurrent-2-hop"):
                self.assertTrue((work / "results" / system / folder).is_dir())

    def test_raw_staging_copies_retained_logs_without_modification(self):
        output, work = self.root / "output", self.root / "plotting"
        (output / "logs").mkdir(parents=True)
        text = b"Delete started.\nMemory consumption: 123MB\nGraphaltyics finished.\n"
        (output / "logs" / "source.log").write_bytes(text)
        records = [dict(name="delete", status="ok", log="/old/run/logs/source.log", metadata=dict(
            suite="gfe", system="radixgraph", dataset="uniform-24", experiment="delete")),
            dict(name="concurrent", status="ok", log="logs/source.log", metadata=dict(
                suite="gfe", system="gtx", dataset="dota-league", experiment="concurrent",
                hop=2, axis="read", count=16)),
            dict(name="failed", status="failed", log="logs/source.log", metadata=dict(
                suite="gfe", system="radixgraph", dataset="graph500-24", experiment="delete"))]
        self.assertEqual(_stage_raw_gfe(output, records, work), [])
        self.assertEqual((work / "results/radixgraph/delete-only/uniform-24").read_bytes(), text)
        self.assertEqual((work / "results/gtx/concurrent-2-hop/dota-16-read-threads").read_bytes(), text)
        self.assertFalse((work / "results/radixgraph/delete-only/graph500-24").exists())

    def test_original_script_runs_unchanged_with_stdin_arguments_and_retained_log(self):
        script = self.root / "original.py"
        script.write_text("import os, sys\n"
                          "print('argument=' + sys.argv[1])\n"
                          "print('stdin=' + input())\n"
                          "print('backend=' + os.environ['MPLBACKEND'])\n"
                          "print('stderr retained', file=sys.stderr)\n")
        before = hashlib.sha256(script.read_bytes()).hexdigest()
        work = self.root / "work"
        result = run_original(script, work, "answer\n", arguments=["literal argument"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["source_sha256"], before)
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(hashlib.sha256(script.read_bytes()).hexdigest(), before)
        self.assertEqual(Path(result["log"]), work / "original.log")
        log = Path(result["log"]).read_text()
        for entry in ("argument=literal argument", "stdin=answer", "backend=Agg", "stderr retained"):
            self.assertIn(entry, log)

    def test_each_original_script_keeps_its_own_log(self):
        work = self.root / "work"
        results = []
        for name in ("plot_all_1", "plot_all_3"):
            script = self.root / (name + ".py")
            script.write_text("print(" + repr(name) + ")\n")
            results.append(run_original(script, work))
        self.assertNotEqual(results[0]["log"], results[1]["log"])
        self.assertEqual(Path(results[0]["log"]).read_text(), "plot_all_1\n")
        self.assertEqual(Path(results[1]["log"]).read_text(), "plot_all_3\n")

    def test_original_script_failure_is_reported_and_logged(self):
        script = self.root / "failing.py"
        script.write_text("import sys\nprint('failure detail', file=sys.stderr)\nsys.exit(7)\n")
        result = run_original(script, self.root / "work")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertTrue(result["source_unchanged"])
        self.assertIn("failure detail", Path(result["log"]).read_text())


if __name__ == "__main__":
    unittest.main()
