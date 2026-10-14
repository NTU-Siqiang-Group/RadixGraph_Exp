import csv
import hashlib
import json
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from upstream_plots import (CONCURRENT_INVOKER, MIXED_INVOKER, _stage_raw_gfe, mixed_checkpoint_series,
                            run_original, write_plot_csv)
from gfe_reports import DATASETS, LABELS, METHODS


CONCURRENT_LAYOUT_FIXTURE = '''import json
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({'font.family': 'Times New Roman'})
scale = float(input())
methods = ['GTX', 'RadixGraph']
num_thread = ['4', '8', '16', '32']
values = [[scale * value for value in row]
          for row in ([0.1, 0.25, 0.4, 0.7], [0.2, 0.4, 0.6, 1.0])]

def plot(ax, throughputs, yaxis, float=False):
    x_base = np.array([0, 0.5, 1, 1.5])
    width = 0.2
    for i, (method, color, hatch) in enumerate(zip(
            methods, ['tab:red', 'tab:purple'], ['-', 'o'])):
        ax.bar(x_base + (i - 0.5) * width, throughputs[i], width,
               label=method, color=color, hatch=hatch, edgecolor='black')
    ax.set_ylabel(yaxis, fontsize=30, fontweight='bold')
    ax.set_xlabel('Number of threads', fontsize=30, fontweight='bold')
    ax.set_xticks(x_base)
    ax.tick_params(axis='y', labelsize=25)
    ax.set_xticklabels(num_thread, fontsize=25)
    if float:
        ax.yaxis.get_offset_text().set_fontsize(35)
        ax.yaxis.offsetText.set_visible(False)
        ax.text(-0.25, 2.5e6, '1e6', fontsize=35,
                va='top', ha='left')
    ax.grid(True, axis='y', linestyle='--', alpha=0.7)

fig, axes = plt.subplots(1, 3, figsize=(22, 7), sharey=False)
plot(axes[0], values, 'Throughput (qops)', float=True)
axes[0].set_xlabel('(a) 1-HN', fontsize=35, fontweight='bold')
plot(axes[1], values, 'Throughput (qops)')
axes[1].set_xlabel('(b) 2-HN', fontsize=35, fontweight='bold')
plot(axes[2], values, 'Throughput (mops)')
axes[2].set_xlabel('(c) Updates', fontsize=35, fontweight='bold')
handles, labels = axes[0].get_legend_handles_labels()
legend = fig.legend(handles, labels, loc='upper center',
                    ncol=len(methods), fontsize=35)
plt.tight_layout(rect=[0, 0, 1, 0.8])
fig.canvas.draw()

def font(text):
    return [text.get_fontfamily(), text.get_fontsize(), text.get_fontweight()]

print(json.dumps({
    'positions': [list(ax.get_position().bounds) for ax in axes],
    'bars': [[bar.get_height() for bar in ax.patches] for ax in axes],
    'labels': [[ax.get_xlabel(), ax.get_ylabel()] for ax in axes],
    'label_fonts': [[font(ax.xaxis.label), font(ax.yaxis.label)] for ax in axes],
    'tick_fonts': [[font(tick) for tick in ax.get_xticklabels() + ax.get_yticklabels()]
                   for ax in axes],
    'legend': [text.get_text() for text in legend.get_texts()],
    'legend_fonts': [font(text) for text in legend.get_texts()],
    'offset': axes[0].yaxis.get_offset_text().get_text(),
    'offset_visible': axes[0].yaxis.get_offset_text().get_visible(),
    'offset_font': font(axes[0].yaxis.get_offset_text()),
    'family': plt.rcParams['font.family'],
}))
plt.close(fig)
'''


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

    def test_completed_gtx_mixed_run_uses_full_measured_progress(self):
        observations = [(10.2, 57), (20.1, 111), (30.1, 161), (40.1, 213),
                        (50.0088, 285), (60.1, 350), (70.1, 412), (80.1, 461),
                        (90.1, 509), (99.9839, 579), (100, 579.922002)]
        rows = [dict(system="gtx", dataset="graph500-24", status="ok",
                     progress_pct=str(progress), elapsed_s=str(elapsed))
                for progress, elapsed in observations]
        series = mixed_checkpoint_series(list(reversed(rows)))
        gtx = series["graph500-24"][list(METHODS).index("gtx")]
        self.assertEqual(gtx, [57, 111, 161, 213, 285, 350, 412, 461, 509, 579.922002])
        self.assertEqual(len(gtx), 10)

    def test_partial_mixed_run_preserves_twenty_percent_extent(self):
        rows = [dict(system="gtx", dataset="uniform-24", status="incomplete_workload",
                     progress_pct=progress, elapsed_s=elapsed)
                for progress, elapsed in ((10.1, 41), (20.1, 80), (20.7, 85))]
        series = mixed_checkpoint_series(rows)
        self.assertEqual(series["uniform-24"][list(METHODS).index("gtx")], [41, 80])

    def test_mixed_series_preserves_all_method_and_dataset_positions(self):
        datasets = ("graph500-24", "uniform-24")
        rows = [dict(system=system, dataset=dataset, status="ok",
                     progress_pct=100, elapsed_s=100 * graph + method + 1)
                for graph, dataset in enumerate(datasets)
                for method, system in enumerate(METHODS)]
        series = mixed_checkpoint_series(list(reversed(rows)))
        self.assertEqual(set(series), set(datasets))
        for graph, dataset in enumerate(datasets):
            self.assertEqual(series[dataset], [[100 * graph + method + 1] * 10
                                              for method in range(len(METHODS))])

    def test_fast_mixed_sample_can_cross_multiple_checkpoints(self):
        rows = [dict(system="radixgraph", dataset="uniform-24", status="ok",
                     progress_pct=progress, elapsed_s=elapsed)
                for progress, elapsed in ((0, 0), (24, 1), (100, 1.234567))]
        series = mixed_checkpoint_series(rows)
        self.assertEqual(series["uniform-24"][-1], [1, 1] + [1.234567] * 8)

    def test_mixed_series_accepts_observed_failed_progress_and_ignores_invalid_rows(self):
        rows = [dict(system="gtx", dataset="graph500-24", status=status,
                     progress_pct=progress, elapsed_s=elapsed)
                for status, progress, elapsed in (
                    ("ok", 10, 1), ("incomplete_workload", 20, 2),
                    ("timeout", 30, 3), ("failed", 40, 4),
                    ("parse_failed", 100, 4.1), ("expected_omission", 100, 4.2),
                    ("running", 100, 4.3), ("ok", "invalid", 5),
                    ("ok", "NaN", 5), ("ok", "inf", 5), ("ok", -1, 5),
                    ("ok", 101, 5), ("ok", 50, "invalid"),
                    ("ok", 50, "NaN"), ("ok", 50, "inf"), ("ok", 50, -1),
                    ("ok", None, 5), ("ok", 50, None))]
        rows.extend([dict(system="unknown", dataset="graph500-24", status="ok",
                          progress_pct=100, elapsed_s=5),
                     dict(system="gtx", dataset="dota-league", status="ok",
                          progress_pct=100, elapsed_s=5)])
        series = mixed_checkpoint_series(rows)
        self.assertEqual(series["graph500-24"][list(METHODS).index("gtx")], [1, 2, 3, 4])
        self.assertTrue(all(not values for values in series["uniform-24"]))

    def test_mixed_invoker_injects_series_and_preserves_upstream_deletion_and_drawing(self):
        source = self.root / "upstream.py"
        source.write_text("import json\n"
                          "mixed_g500 = [[] for _ in range(5)]\n"
                          "mixed_u24 = [[] for _ in range(5)]\n"
                          "def read_results_mixed(path):\n"
                          "    raise RuntimeError('incorrect upstream reader ran')\n"
                          "def read_results_delete(path):\n"
                          "    print('deletion reader retained ' + path)\n"
                          "read_results_mixed('./results')\n"
                          "read_results_delete('./results')\n"
                          "print(json.dumps({'graph500-24': mixed_g500, 'uniform-24': mixed_u24}))\n"
                          "print('drawing code retained')\n")
        before = source.read_bytes()
        invoker = self.root / "mixed_invoker.py"
        invoker.write_text(MIXED_INVOKER)
        series = {"graph500-24": [[], [], [], [57, 111, 161], [10, 20]],
                  "uniform-24": [[1], [2], [3], [99, 154], [8, 16]]}
        result = run_original(invoker, self.root / "work", json.dumps(
            {"source": str(source), "series": series}))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(source.read_bytes(), before)
        log = Path(result["log"]).read_text()
        self.assertIn("deletion reader retained ./results", log)
        self.assertIn("drawing code retained", log)
        self.assertIn(json.dumps(series), log)
        self.assertNotIn("incorrect upstream reader ran", log)

    def test_mixed_invoker_rejects_changed_upstream_call_layout(self):
        invoker = self.root / "mixed_invoker.py"
        invoker.write_text(MIXED_INVOKER)
        series = {dataset: [[] for _ in METHODS] for dataset in ("graph500-24", "uniform-24")}
        for name, calls in (("absent", "pass\n"),
                            ("nested", "if True:\n    read_results_mixed('./results')\n"),
                            ("duplicate", "read_results_mixed('./results')\nread_results_mixed('./results')\n")):
            with self.subTest(layout=name):
                source = self.root / (name + ".py")
                source.write_text("def read_results_mixed(path):\n    pass\n" + calls)
                before = source.read_bytes()
                result = run_original(invoker, self.root / name, json.dumps(
                    {"source": str(source), "series": series}))
                self.assertEqual(result["status"], "failed")
                self.assertIn("read_results_mixed", Path(result["log"]).read_text())
                self.assertEqual(source.read_bytes(), before)

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'Matplotlib is not installed')
    def test_concurrent_invoker_keeps_layout_fonts_and_values_across_scales(self):
        source = self.root / 'concurrent.py'
        source.write_text(CONCURRENT_LAYOUT_FIXTURE)
        before = source.read_bytes()
        invoker = self.root / 'concurrent_invoker.py'
        invoker.write_text(CONCURRENT_INVOKER)
        family = ['Times New Roman']
        for scale, offset in ((250000, ''), (1500000, '1e6'), (15000000, '1e7')):
            with self.subTest(scale=scale):
                result = run_original(invoker, self.root / str(scale), str(scale) + '\n',
                                      arguments=[source])
                log = Path(result['log']).read_text()
                self.assertEqual(result['status'], 'ok', log)
                self.assertTrue(result['source_unchanged'])
                self.assertEqual(source.read_bytes(), before)
                drawing = json.loads(log.splitlines()[-1])
                self.assertTrue(all(position[3] > 0.45 for position in drawing['positions']),
                                drawing['positions'])
                self.assertEqual(drawing['offset'], offset)
                self.assertTrue(drawing['offset_visible'])
                self.assertEqual(drawing['offset_font'], [family, 35, 'normal'])
                self.assertEqual(drawing['family'], family)
                self.assertEqual(drawing['labels'], [
                    ['(a) 1-HN', 'Throughput (qops)'],
                    ['(b) 2-HN', 'Throughput (qops)'],
                    ['(c) Updates', 'Throughput (mops)']])
                self.assertEqual(drawing['label_fonts'],
                                 [[[family, 35, 'bold'], [family, 30, 'bold']]] * 3)
                self.assertTrue(all(font == [family, 25, 'normal']
                                    for ticks in drawing['tick_fonts'] for font in ticks))
                self.assertEqual(drawing['legend'], ['GTX', 'RadixGraph'])
                self.assertEqual(drawing['legend_fonts'], [[family, 35, 'normal']] * 2)
                expected_values = [scale * value for value in
                                   (0.1, 0.25, 0.4, 0.7, 0.2, 0.4, 0.6, 1.0)]
                self.assertEqual(drawing['bars'], [expected_values] * 3)

    def test_concurrent_invoker_rejects_unexpected_layout_before_execution(self):
        source = self.root / 'concurrent.py'
        source.write_text("print('upstream executed')\n" + CONCURRENT_LAYOUT_FIXTURE.replace(
            "ax.text(-0.25, 2.5e6, '1e6'", "ax.text(-0.25, 3e6, '1e6'"))
        before = source.read_bytes()
        invoker = self.root / 'concurrent_invoker.py'
        invoker.write_text(CONCURRENT_INVOKER)
        result = run_original(invoker, self.root / 'work', '1500000\n', arguments=[source])
        log = Path(result['log']).read_text()
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('upstream executed', log)
        self.assertEqual(source.read_bytes(), before)

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
