"""Image capability selection and ARM runs without the x86-only Spruce archive."""
from contextlib import redirect_stderr
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import DATASETS, SYSTEMS, Pipeline, export, main, parser
from gfe_reports import render_gfe_reports
from upstream_plots import render_original_plots


ARM_SOURCES = dict(architecture="arm64",
                   available_systems=[name for name in SYSTEMS if name != "bvgt"],
                   excluded_systems={"bvgt": "Spruce/bvgt requires x86-64"})


class RuntimeCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def arguments(self, *extra):
        return ["--profile", "smoke", "--run-dir", str(self.root / "run"), *extra]

    def test_source_runs_keep_x86_defaults_and_exclude_spruce_on_arm(self):
        with patch("pipeline.load_source_versions", return_value={}):
            for machine in ("x86_64", "amd64", "aarch64", "arm64"):
                with self.subTest(machine=machine), patch("pipeline.platform.machine", return_value=machine):
                    args = parser().parse_args(self.arguments())
                    expected = list(SYSTEMS) if machine in ("x86_64", "amd64") else ARM_SOURCES["available_systems"]
                    self.assertEqual(args.systems, expected)
                    p = Pipeline(args)
                    self.assertEqual(p.manifest["capabilities"]["available_systems"], expected)
                    p.manifest_path.unlink()

    def test_image_manifest_controls_defaults_when_running_under_emulation(self):
        for sources, host in ((ARM_SOURCES, "x86_64"),
                              (dict(architecture="amd64", available_systems=list(SYSTEMS)), "aarch64")):
            with self.subTest(architecture=sources["architecture"]):
                with patch("pipeline.load_source_versions", return_value=sources):
                    with patch("pipeline.platform.machine", return_value=host):
                        args = parser().parse_args(self.arguments())
                        p = Pipeline(args)
                self.assertEqual(args.systems, sources["available_systems"])
                self.assertEqual(p.manifest["capabilities"]["architecture"], sources["architecture"])
                self.assertEqual(p.manifest["sources"], sources)
                p.manifest_path.unlink()

    def test_explicit_spruce_selection_fails_before_starting_work(self):
        arguments = ["run.sh", *self.arguments("--systems", "radixgraph,bvgt")]
        with patch("pipeline.load_source_versions", return_value=ARM_SOURCES):
            with patch.object(sys, "argv", arguments), patch("preflight.check_requirements") as requirements:
                with redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as failure:
                    main()
        self.assertEqual(failure.exception.code, 2)
        self.assertIn("bvgt: Spruce/bvgt requires x86-64", errors.getvalue())
        requirements.assert_not_called()
        self.assertFalse((self.root / "run").exists())

    def test_arm_plan_and_manifest_include_only_selected_available_systems(self):
        with patch("pipeline.load_source_versions", return_value=ARM_SOURCES):
            p = Pipeline(parser().parse_args(self.arguments("--skip-prepare")))
        self.assertEqual(set(system for system, *_ in p.main_jobs()), set(ARM_SOURCES["available_systems"]))
        self.assertFalse(any("bvgt" in name for name in p.planned_jobs({"main", "studies", "batch"})))
        stored = json.loads(p.manifest_path.read_text())
        self.assertEqual(stored["config"]["systems"], ARM_SOURCES["available_systems"])
        self.assertEqual(stored["capabilities"]["excluded_systems"], ARM_SOURCES["excluded_systems"])

    def test_arm_reports_leave_spruce_unmeasured_and_keep_other_systems(self):
        with patch("pipeline.load_source_versions", return_value=ARM_SOURCES):
            p = Pipeline(parser().parse_args(self.arguments()))
        log = p.output / "logs" / "fixture.log"
        log.write_text("Loaded 1000 edges\nMemory consumption: 1 MB\n"
                       "Insertions performed with 2 threads in 1 s\n"
                       "Deletions performed with 2 threads in 2 s\n"
                       "Vertex queries performed with 2 threads in 3 s\n")
        records = [dict(name=f"{system}-{dataset}-{experiment}", status="ok", log=str(log),
                        metadata=dict(suite="gfe", system=SYSTEMS[system],
                                      dataset=dataset, experiment=experiment))
                   for system in p.args.systems for dataset in DATASETS
                   for experiment in ("random", "vertices")]
        self.assertEqual(render_gfe_reports(p.output, records)["issues"], [])
        with (p.output / "tables" / "figure8.csv").open() as stream:
            measurements = list(csv.DictReader(stream))
        self.assertEqual({row["system"] for row in measurements},
                         {SYSTEMS[system] for system in p.args.systems})

        def render_fixture(script, work, input_text=None):
            # The original renderer keeps a fixed five-method layout. Its
            # adapter must pass an empty Spruce row, retaining every ARM value.
            for filename in ("edge_insertion", "edge_deletion", "memory",
                             "vertex_insertion", "vertex_query", "vertex_memory"):
                with (work / "csv" / (filename + ".csv")).open() as stream:
                    rows = {row[0]: row[1:-1] for row in list(csv.reader(stream))[1:]}
                self.assertEqual(rows.pop("Spruce"), ["NaN"] * len(DATASETS))
                self.assertTrue(all(all(float(value) > 0 for value in row)
                                    for row in rows.values()))
            figures = work / "figures"
            figures.mkdir(exist_ok=True)
            (figures / "combined_plots1.pdf").touch()
            return dict(status="ok")

        with patch("upstream_plots.run_original", side_effect=render_fixture):
            report = render_original_plots(p.output, records, self.root / "gfe", self.root / "radix", "smoke")
        self.assertEqual(report["issues"], [])
        self.assertTrue((p.output / "figures" / "figure8.pdf").is_file())

    def test_report_regeneration_retains_original_image_capabilities(self):
        with patch("pipeline.load_source_versions", return_value=ARM_SOURCES):
            original = Pipeline(parser().parse_args(self.arguments()))
        with patch("pipeline.load_source_versions", return_value={}):
            with patch("pipeline.platform.machine", return_value="x86_64"):
                regenerated = Pipeline(parser().parse_args(self.arguments("--stages", "report")))
        self.assertEqual(regenerated.capabilities, original.capabilities)
        self.assertEqual(regenerated.manifest["sources"], ARM_SOURCES)
        self.assertEqual(regenerated.manifest["config"]["systems"], ARM_SOURCES["available_systems"])

    def test_arm_coverage_distinguishes_available_grid_from_full_paper_grid(self):
        with patch("pipeline.load_source_versions", return_value=ARM_SOURCES):
            p = Pipeline(parser().parse_args(self.arguments()))
        # Export coverage after successful renderers have produced the complete
        # smoke artifact set. Only the unavailable Spruce measurements are absent.
        expected = ["figures/figure8.pdf", "figures/figure9.pdf", "figures/figure10.pdf",
                    "figures/figure12_a.pdf", "figures/figure12_b.pdf", "figures/figure13.pdf",
                    "tables/figure12_transformations.csv", "tables/table7_baselines.csv"]
        expected += [f"{directory}/table{number}.{extension}" for number in (5, 6, 7)
                     for directory, extension in (("tables", "csv"), ("figures", "pdf"))]
        for name in expected:
            path = p.output / name
            path.parent.mkdir(exist_ok=True)
            path.touch()
        plotting = dict(plots=[], issues=[])
        with patch("gfe_reports.render_gfe_reports", return_value={}):
            with patch("study_reports.render_study_reports", return_value={}):
                with patch("batch_baselines.render_batch_reports", return_value={}):
                    with patch("table_figures.render_table_figures", return_value=dict(figures=[], tables=[], issues=[])):
                        with patch("upstream_plots.render_original_plots", return_value=plotting):
                            self.assertEqual(export(p), [])
        coverage = json.loads((p.output / "coverage.json").read_text())
        self.assertTrue(coverage["full_available_scope"])
        self.assertFalse(coverage["full_experiment_scope"])
        self.assertEqual(coverage["missing_artifacts"], [])
        self.assertEqual(coverage["selected_systems"], ARM_SOURCES["available_systems"])
        self.assertEqual(coverage["capabilities"], p.capabilities)
        self.assertEqual(p.manifest["status"], "finished")
        readme = (p.output / "README.md").read_text()
        self.assertIn("Architecture: **arm64**", readme)
        self.assertIn("bvgt: Spruce/bvgt requires x86-64", readme)


if __name__ == "__main__":
    unittest.main()
