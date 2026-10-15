from contextlib import ExitStack, contextmanager, redirect_stdout
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import Pipeline, export, main, parser


class ReportSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def arguments(self, profile, name, *selection):
        return ["--profile", profile, "--run-dir", str(self.root / name),
                "--gfe-dir", str(self.root / "gfe"), "--radix-dir", str(self.root / "radix"),
                "--data-dir", str(self.root / "data"), "--datasets", "com-lj.ungraph",
                "--systems", "radixgraph", "--no-progress", *selection]

    def figure_pdfs(self, profile, main_figures=True):
        names = ["figure12_a", "figure12_b", "figure13"]
        if main_figures:
            names += ["figure8", "figure10"]
        if profile == "paper":
            names.append("figure12_c")
        return [f"figures/{name}.pdf" for name in names]

    def seed_studies(self, p, malformed_tables=False):
        work = p.output / "studies" / "work" / "figure12-transformation"
        work.mkdir(parents=True)
        (work / "memory_log.txt").write_text("1000 10000\n3100 31000\n")
        transform = p.output / "logs" / "transform.log"
        transform.write_text("n = 1000, a = 19 4 3 3 3\n"
                             "n = 3100, a = 20 3 3 3 3\n"
                             "Transformation takes 0.1 s\n")
        workload = p.output / "logs" / "workload.log"
        workload.write_text("\n".join(
            f"{method}: memory after {n} insertions is {n * 100} bytes"
            for method in ("SORT", "vEB") for n in range(1000, 10001, 1000)))
        records = [dict(name="figure12-transformation", status="ok", log=str(transform),
                        workdir=str(work), metadata=dict(artifact="figure12", kind="transformation")),
                   dict(name="figure13-workload-u", status="ok", log=str(workload),
                        metadata=dict(artifact="figure13", kind="workload", workload="u", n=10000))]
        if malformed_tables:
            # Missing required metadata makes these fail if an unselected table is parsed.
            records += [dict(name=artifact, status="ok", log="logs/unused.log",
                             metadata=dict(artifact=artifact, method="RadixGraph"))
                        for artifact in ("table5", "table6", "table7")]
        (p.output / "studies" / "records.json").write_text(json.dumps(records))

    def seed_retained_reports(self, p):
        names = [f"tables/{name}.{suffix}" for name in ("table5", "table6", "table7")
                 for suffix in ("csv", "md")]
        names += [f"figures/table{n}.pdf" for n in (5, 6, 7)]
        names += [f"tables/{name}.csv" for name in
                  ("table6_updates", "table6_analytics", "table7_radixgraph", "table7_baselines")]
        names += ["tables/table7_baselines_notes.md", "batch/measurements.json", "batch/report.json"]
        retained = {}
        for name in names:
            path = p.output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            retained[name] = f"Retained report: {name}\n".encode()
            path.write_bytes(retained[name])
        p.jobs.append(dict(name="batch-malformed", status="ok", log="logs/nonexistent.log",
                           metadata=dict(suite="batch_baselines")))
        p.save()
        return retained

    @contextmanager
    def renderers(self, p, figure_paths):
        def draw_figures(*args):
            for name in figure_paths:
                path = p.output / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"mock upstream PDF")
            return dict(plots=[], issues=[])

        def draw_tables(output, numbers=(5, 6, 7)):
            paths = [f"figures/table{n}.pdf" for n in numbers]
            for name in paths:
                path = Path(output) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"mock table PDF")
            return dict(figures=paths, tables=[], issues=[])

        def batch_reports(output):
            path = Path(output) / "tables" / "table7_baselines.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("dataset,method,batch_size,insert_ops_s,delete_ops_s,memory_gib,status,source_log\n")
            return dict(issues=[], rows=0)

        with ExitStack() as stack:
            stack.enter_context(patch("gfe_reports.render_gfe_reports", return_value=dict(issues=[])))
            stack.enter_context(patch("upstream_plots.render_original_plots", side_effect=draw_figures))
            tables = stack.enter_context(patch("table_figures.render_table_figures", side_effect=draw_tables))
            batch = stack.enter_context(patch("batch_baselines.render_batch_reports", side_effect=batch_reports))
            yield tables, batch

    def assert_figure_only_report(self, p, retained):
        coverage = json.loads((p.output / "coverage.json").read_text())
        expected = {*self.figure_pdfs(p.profile), "tables/figure12_transformations.csv"}
        self.assertEqual(set(coverage["expected_artifacts"]), expected)
        self.assertEqual(coverage["missing_artifacts"], [])
        self.assertFalse(coverage["full_experiment_scope"])
        self.assertEqual(p.manifest["table_figures"]["figures"], [])
        for name, content in retained.items():
            self.assertEqual((p.output / name).read_bytes(), content, name)
        with (p.output / "tables" / "figure12_transformations.csv").open() as stream:
            self.assertEqual(next(csv.DictReader(stream))["seconds"], "0.1")
        with (p.output / "tables" / "figure13_workloads.csv").open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 20)

    def test_default_and_all_exports_skip_tables_and_batch_in_both_profiles(self):
        for profile in ("smoke", "paper"):
            for label, selection in (("default", ()), ("all", ("--stages", "all"))):
                with self.subTest(profile=profile, selection=label):
                    p = Pipeline(parser().parse_args(self.arguments(profile, f"{profile}-{label}", *selection)))
                    self.seed_studies(p, malformed_tables=True)
                    retained = self.seed_retained_reports(p)
                    with self.renderers(p, self.figure_pdfs(profile)) as (tables, batch):
                        self.assertEqual(export(p), [])
                    tables.assert_not_called()
                    batch.assert_not_called()
                    self.assert_figure_only_report(p, retained)

    def test_report_cli_skips_saved_table_and_batch_selections_in_both_profiles(self):
        for profile in ("smoke", "paper"):
            for label, selection in (("alias", ("--report",)), ("stage", ("--stages", "report"))):
                with self.subTest(profile=profile, selection=label):
                    name = f"report-{profile}-{label}"
                    # Saved runs may include both explicit table/batch selections and the studies alias.
                    p = Pipeline(parser().parse_args(self.arguments(
                        profile, name, "--stages", "all,studies,tables,batch")))
                    self.seed_studies(p, malformed_tables=True)
                    retained = self.seed_retained_reports(p)
                    arguments = self.arguments(profile, name, *selection)
                    self.assertEqual(parser().parse_args(arguments).stages, ["report"])
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(sys, "argv", ["run.sh", *arguments]))
                        stack.enter_context(patch("preflight.check_requirements", return_value=dict(status="ok")))
                        stack.enter_context(patch("preflight.print_requirements"))
                        stack.enter_context(redirect_stdout(io.StringIO()))
                        prepare = stack.enter_context(patch("pipeline.Pipeline.run"))
                        main_jobs = stack.enter_context(patch("pipeline.Pipeline.run_main"))
                        studies = stack.enter_context(patch("studies.run_studies"))
                        batch_jobs = stack.enter_context(patch("batch_baselines.run_batch_baselines"))
                        tables, batch = stack.enter_context(self.renderers(p, self.figure_pdfs(profile)))
                        self.assertEqual(main(), 0)
                    for method in (prepare, main_jobs, studies, batch_jobs, tables, batch):
                        method.assert_not_called()
                    p.manifest = json.loads(p.manifest_path.read_text())
                    self.assert_figure_only_report(p, retained)

    def test_explicit_table_stages_keep_selected_automatic_reports(self):
        cases = (("tables", (5, 6, 7), False), ("studies", (5, 6, 7), False),
                 ("batch", (7,), True))
        for profile in ("smoke", "paper"):
            for stage, numbers, batch_selected in cases:
                with self.subTest(profile=profile, stage=stage):
                    p = Pipeline(parser().parse_args(self.arguments(
                        profile, f"explicit-{profile}-{stage}", "--stages", stage)))
                    self.seed_studies(p)
                    figures = self.figure_pdfs(profile, main_figures=False) if stage == "studies" else []
                    with self.renderers(p, figures) as (tables, batch):
                        self.assertEqual(export(p), [])
                    tables.assert_called_once_with(p.output, numbers=numbers)
                    self.assertEqual(batch.call_count, int(batch_selected))
                    coverage = json.loads((p.output / "coverage.json").read_text())
                    self.assertEqual(coverage["missing_artifacts"], [])
                    for number in numbers:
                        self.assertIn(f"tables/table{number}.csv", coverage["expected_artifacts"])
                        self.assertIn(f"figures/table{number}.pdf", coverage["expected_artifacts"])
                    if stage == "batch":
                        self.assertEqual(set(coverage["expected_artifacts"]),
                                         {"tables/table7_baselines.csv", "tables/table7.csv", "figures/table7.pdf"})
                        self.assertFalse((p.output / "tables" / "table5.csv").exists())
                        self.assertFalse((p.output / "tables" / "table6.csv").exists())


if __name__ == "__main__":
    unittest.main()
