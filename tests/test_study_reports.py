import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from scripts.study_reports import (parse_analytics, parse_batch, parse_transform,
                                   parse_vertex_index, render_study_reports, _read)
from scripts.studies import find_trailing_fanout, run_studies
from scripts.upstream_study_plots import render_study_plots


class StudyReportTests(unittest.TestCase):
    def test_exported_run_reads_retained_logs_even_when_original_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, retained = Path(tmp) / "original", Path(tmp) / "retained"
            log = original / "logs" / "index.log"
            copy = retained / "logs" / "index.log"
            log.parent.mkdir(parents=True)
            copy.parent.mkdir(parents=True)
            log.write_text("original")
            copy.write_text("retained")
            self.assertEqual(_read(retained, str(log)), "retained")
            copy.unlink()
            self.assertEqual(_read(retained, str(log)), "")

    def test_trailing_fanout_finds_previous_distinct_interval(self):
        updated, previous = [19, 4, 3, 3, 3], [18, 5, 3, 3, 3]
        queries = []

        def optimize(n):
            queries.append(n)
            return updated if n >= 40000 else previous

        result = find_trailing_fanout(100000, updated,
                                      [{"n": 100000, "fanout": updated}], optimize)
        self.assertEqual(result, previous)
        self.assertIn(50000, queries)
        self.assertIn(39999, queries)
        self.assertLess(len(queries), 25)

    def test_trailing_fanout_uses_matching_measured_change_points(self):
        updated, previous = [20, 3, 3, 3, 3], [19, 4, 3, 3, 3]
        result = find_trailing_fanout(1000000, updated,
                                      [{"n": 100000, "fanout": previous},
                                       {"n": 310000, "fanout": updated}],
                                      lambda _: self.fail("Measured transitions already identify the predecessor"))
        self.assertEqual(result, previous)
        self.assertIsNone(find_trailing_fanout(100, updated, [], lambda _: updated))

    def test_paper_runner_supplies_stdin_and_includes_real_dataset_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = []
            specs = []
            for name in ("com-lj.ungraph", "com-orkut.ungraph", "twitter-2010"):
                edge = root / f"{name}.e"
                edge.write_text("0 1\n")
                specs.append({"name": name, "edge": edge})
            pipeline = SimpleNamespace(output=root, radix=root / "RadixGraph",
                                       profile="paper", threads=4,
                                       dataset_specs=specs, jobs=jobs)

            def run(name, command, cwd, input_text=None, metadata=None):
                kind, artifact = metadata["kind"], metadata["artifact"]
                text = ""
                if kind == "optimizer":
                    self.assertEqual(input_text.split()[1:], ["32", "5"])
                    n = int(input_text.split()[0])
                    fanout = "19 4 3 3 3" if n >= 40000 else "18 5 3 3 3" if n >= 500 else "17 6 3 3 3"
                    (cwd / "settings.txt").write_text(f"5\n{fanout}\n")
                elif kind == "transformation":
                    text = "n = 100000, a = 19 4 3 3 3\n"
                    (cwd / "memory_log.txt").write_text("10000000 1000000\n")
                elif kind == "memory":
                    self.assertEqual(len(input_text.split()), 8)
                    text = "Allocated space of your Trie: 1000000 bytes\n"
                elif kind == "workload":
                    self.assertEqual(input_text.split(), ["10000000", "32", metadata["workload"]])
                    text = "\n".join(f"{method}: memory after {n} insertions is {n * 10} bytes"
                                     for method in ("SORT", "vEB") for n in range(1000000, 10000001, 1000000))
                elif kind == "index":
                    self.assertEqual(input_text.split(), [str(metadata["n"]), str(metadata["bits"])])
                    text = "\n".join(f"Memory used by {method}: 1024 KB\nThroughput for {method} insertion: 1000 ops\nThroughput for {method} query: 2000 ops"
                                     for method in ("ART", "SORT"))
                elif kind == "updates":
                    text = "Memory used by RadixGraph: 1024 KB\nTime for RadixGraph insertion: 2s\nTime for RadixGraph deletion: 3s\n"
                elif kind == "analytics":
                    text = "2-hop neighbors: time = 2s\nBFS: time = 4s\nSSSP: time = 5s\nBC: time = 6s\n"
                elif kind == "batch":
                    text = "Memory usage: 0.5 GB\n" + "\n".join(f"Batch size: {batch}, average insert throughput: 1000 ops/s, average delete throughput: 2000 ops/s"
                                                                              for batch in (10, 100, 1000, 10000))
                log = cwd / "stdout.log"
                log.write_text(text)
                result = {"name": name, "status": "ok", "log": str(log),
                          "exit_code": 0, "metadata": metadata, "input_text": input_text}
                jobs.append(result)
                return result

            def note(name, status, reason, metadata=None):
                jobs.append({"name": name, "status": status, "reason": reason})

            pipeline.run, pipeline.note = run, note
            run_studies(pipeline)
            batches = [job for job in jobs if job.get("metadata", {}).get("artifact") == "table7"]
            self.assertEqual([job["metadata"]["dataset"] for job in batches], [spec["name"] for spec in specs])
            indexes = [job for job in jobs if job.get("metadata", {}).get("artifact") == "table5"]
            self.assertEqual(len(indexes), 8)
            self.assertTrue(all(job["status"] == "ok" for job in jobs))

    def test_transform_pairs_timings_with_change_points(self):
        text = """Optimal parameters for different n:
n = 100000, a = 19 4 3 3 3
n = 310000, a = 20 3 3 3 3
Transforming to n = 310000, a = 20 3 3 3 3
Transformation takes 2.3e-1 s
"""
        configs, times = parse_transform(text)
        self.assertEqual(len(configs), 2)
        self.assertEqual(times, [{"n": 310000, "seconds": .23}])

    def test_partial_vertex_output_retains_missing_art(self):
        text = """Memory used by SORT: 1234 KB
Throughput for SORT insertion: 1.2e+06 ops
Throughput for SORT query: 9.6e+07 ops
"""
        sort, art = parse_vertex_index(text)
        self.assertEqual(sort["memory_kib"], 1234)
        self.assertEqual(sort["insert_ops_s"], 1200000)
        self.assertIsNone(art["insert_ops_s"])

    def test_batch_does_not_invent_missing_rows(self):
        rows = parse_batch("""Memory usage: 0.75 GB
Batch size: 10, average insert time: 1e-5s, average delete time: 2e-5s
Batch size: 10, average insert throughput: 1e6 ops/s, average delete throughput: 5e5 ops/s
""")
        self.assertEqual(rows[0]["insert_ops_s"], 1e6)
        self.assertEqual(rows[0]["delete_ops_s"], 5e5)
        self.assertEqual(rows[0]["memory_gib"], .75)
        self.assertIsNone(rows[1]["insert_ops_s"])
        self.assertEqual([row["batch_size"] for row in rows], [10, 100, 1000, 10000])

    def test_analytics_ignores_non_finite_values(self):
        data = parse_analytics("BFS: time = nans\nSSSP: time = 1.25s\n")
        self.assertIsNone(data["bfs_seconds"])
        self.assertEqual(data["sssp_seconds"], 1.25)

    def test_saved_logs_rerender_ratios_and_merge_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "studies").mkdir()
            (root / "logs").mkdir()
            (root / "tables").mkdir()
            records = []

            def record(name, kind, variant, text, status="ok"):
                relative = Path("logs") / f"{name}.log"
                (root / relative).write_text(text)
                # Paths intentionally refer to a former Docker mount.
                records.append({"name": name, "status": status,
                                "log": str(Path("/old-mount") / relative),
                                "metadata": {"artifact": "table6", "kind": kind,
                                             "dataset": "lj", "variant": variant}})

            record("sort-up", "updates", "sort", "Memory used by RadixGraph: 1024 KB\nTime for RadixGraph insertion: 2s\nTime for RadixGraph deletion: 3s\n")
            record("art-up", "updates", "art", "Memory used by RadixGraph: 2048 KB\nTime for RadixGraph insertion: 10s\nTime for RadixGraph deletion: 12s\n")
            record("sort-an", "analytics", "sort", "2-hop neighbors: time = 2s\nBFS: time = 4s\nSSSP: time = 5s\nBC: time = 6s\n")
            record("no-chain-an", "analytics", "sort-no-chain", "2-hop neighbors: time = 6s\nBFS: time = 8s\nSSSP: time = 10s\nBC: time = 18s\n")
            (root / "studies" / "records.json").write_text(json.dumps(records))
            (root / "tables" / "table7_baselines.csv").write_text("dataset,method,batch_size,insert_ops_s,delete_ops_s,memory_gib,status,source_log\nlj,Terrace,10,100,50,1.2,ok,logs/terrace.log\n")
            report = render_study_reports(root)
            with (root / "tables" / "table6.csv").open() as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["art_sort_insert_ratio"], "5.0")
            self.assertEqual(row["art_sort_delete_ratio"], "4.0")
            self.assertEqual(row["art_sort_memory_ratio"], "2.0")
            self.assertEqual(row["no_chain_two_hop_ratio"], "3.0")
            self.assertEqual(row["status"], "ok")
            self.assertEqual(report["rows"]["table7"], 1)
            self.assertIn("Terrace", (root / "tables" / "table7.md").read_text())
            # A failed input is explicit and may not generate a ratio.
            records[1]["status"] = "failed"
            render_study_reports(root, records)
            with (root / "tables" / "table6.csv").open() as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["art_sort_insert_ratio"], "")
            self.assertEqual(row["status"], "incomplete")

    def test_figure_measurements_export_without_a_replacement_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "studies" / "work" / "figure12-transformation"
            work.mkdir(parents=True)
            (root / "logs").mkdir()
            (work / "memory_log.txt").write_text("1000 100000\n10000 1000000\n")
            log = root / "logs" / "transform.log"
            log.write_text("n = 1000, a = 19 4 3 3 3\nn = 3100, a = 20 3 3 3 3\nTransforming to n = 3100, a = 20 3 3 3 3\nTransformation takes 0.1 s\n")
            records = [{"name": "figure12-transformation", "status": "ok",
                        "log": str(log), "workdir": str(work),
                        "metadata": {"artifact": "figure12", "kind": "transformation", "n_max": 10000}}]
            for method in ("Updated", "Trailing", "vEB"):
                log = root / "logs" / f"memory-{method}.log"
                log.write_text("Allocated space of your Trie: 1000000 bytes\n")
                records.append({"status": "ok", "log": str(log),
                                "metadata": {"artifact": "figure12", "kind": "memory", "n": 10000,
                                             "variant": method, "fanout": [19, 4, 3, 3, 3]}})
            for code in ("u", "s", "h"):
                log = root / "logs" / f"workload-{code}.log"
                log.write_text("\n".join(f"{method}: memory after {n} insertions is {n * 100} bytes"
                                         for method in ("SORT", "vEB") for n in range(1000, 10001, 1000)))
                records.append({"status": "ok", "log": str(log),
                                "metadata": {"artifact": "figure13", "kind": "workload", "workload": code, "n": 10000}})
            report = render_study_reports(root, records)
            self.assertFalse(report["issues"])
            self.assertEqual(report["figures"], [])
            self.assertFalse((root / "figures").exists())
            for name in ("figure12_fanouts", "figure12_memory_costs", "figure12_transformations"):
                self.assertTrue((root / "tables" / f"{name}.csv").is_file())
                self.assertTrue((root / "tables" / f"{name}.md").is_file())
            self.assertIn("0.1", (root / "tables" / "figure12_transformations.md").read_text())
            with (root / "tables" / "figure13_workloads.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 60)
            self.assertEqual(rows[0]["memory_bytes"], "100000")

    def test_original_sort_functions_receive_measurements_without_running_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, gfe = root / "output", root / "gfe"
            (output / "tables").mkdir(parents=True)
            (output / "studies").mkdir()
            (gfe / "scripts").mkdir(parents=True)
            original = gfe / "scripts" / "plot_sort.py"
            # These stand-in functions record their arguments instead of drawing.
            # The two example calls model the original module's fixed paper data.
            original.write_text('''import json
from pathlib import Path
STYLE = "original setup"
def plot1(x, y, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([STYLE, x, y]))
def plot2(labels, memory, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([STYLE, labels, memory]))
plot1([999], [[999]], "paper_example_a.pdf")
plot2([999], [[999]], "paper_example_b.pdf")
''')
            original_bytes = original.read_bytes()
            (output / "studies" / "records.json").write_text(json.dumps([
                {"status": "ok", "metadata": {"artifact": "figure12", "kind": "transformation"}}
            ]))
            (output / "tables" / "figure12_fanouts.csv").write_text(
                "n,fanout,source_log\n1000,17 6 3 3 3,logs/transform.log\n10000,18 5 3 3 3,logs/transform.log\n")
            costs = "n,variant,fanout,memory_bytes,status,source_log\n"
            for index, variant in enumerate(("Updated", "Trailing", "vEB"), start=1):
                costs += f"1000,{variant},17 6 3 3 3,{index * 2**20},ok,logs/memory.log\n"
            (output / "tables" / "figure12_memory_costs.csv").write_text(costs)

            def runner(script, cwd, input_text=None):
                result = subprocess.run([sys.executable, str(script)], cwd=cwd,
                                        input=input_text, text=True, capture_output=True, timeout=10)
                return {"status": "ok" if result.returncode == 0 else "failed",
                        "exit_code": result.returncode, "reason": result.stderr}

            report = render_study_plots(output, root / "radix", gfe, "smoke", runner)
            self.assertFalse(report["issues"])
            self.assertEqual(original.read_bytes(), original_bytes)
            fanouts = json.loads((output / "figures" / "figure12_a.pdf").read_text())
            self.assertEqual(fanouts, ["original setup", [1000, 10000], [[17, 18], [6, 5], [3, 3], [3, 3], [3, 3]]])
            memory = json.loads((output / "figures" / "figure12_b.pdf").read_text())
            self.assertEqual(memory, ["original setup", ["$10^{3}$"], [[1.0], [2.0], [3.0]]])
            self.assertFalse(list(output.rglob("paper_example_*.pdf")))
            footprint = next(row for row in report["plots"] if row["artifact"] == "figure12_c")
            self.assertEqual(footprint["status"], "skipped")
            self.assertIn("x-axis", footprint["reason"])
            self.assertTrue(all(row["source_unchanged"] for row in report["plots"] if row["status"] == "ok"))
            # A successful process without a new PDF cannot reuse an old work file.
            report = render_study_plots(output, root / "radix", gfe, "smoke",
                                        lambda *args, **kwargs: {"status": "ok"})
            self.assertEqual(len(report["issues"]), 2)
            self.assertTrue(all(row["status"] == "failed" for row in report["plots"]
                                if row["artifact"] in ("figure12_a", "figure12_b")))

    def test_original_study_adapter_has_no_work_for_runs_without_studies(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            output.mkdir()

            def unexpected(*args, **kwargs):
                self.fail("Original study plotting should not run without study measurements")

            report = render_study_plots(output, Path(tmp) / "radix", Path(tmp) / "gfe",
                                        "paper", unexpected)
            self.assertEqual(report, {"plots": [], "issues": []})
            self.assertFalse((output / "upstream").exists())


if __name__ == "__main__":
    unittest.main()
