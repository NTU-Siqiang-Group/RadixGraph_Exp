import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.table_figures import _draw_table, _render_pages, format_paper_tables, parse_html_tables, render_table_figures


TABLE = """<table>
<thead><tr><th rowspan="2">Graphs</th><th colspan="2">Results</th></tr>
<tr><th>u = 2<sup>24</sup></th><th>Method</th></tr></thead>
<tbody><tr><th>Graph &amp; &lt;x&gt;</th><td><strong>1.23×</strong></td>
<td>Insert<br>throughput</td></tr></tbody></table>"""

FIELDS = {
    5: ("n", "bits", "method", "insert_ops_s", "query_ops_s", "memory_kib", "status", "source_log"),
    6: ("dataset", "art_sort_insert_ratio", "art_sort_delete_ratio", "art_sort_memory_ratio",
        "no_chain_two_hop_ratio", "no_chain_bfs_ratio", "no_chain_sssp_ratio", "no_chain_bc_ratio", "status", "source_logs"),
    7: ("dataset", "method", "batch_size", "insert_ops_s", "delete_ops_s", "memory_gib", "status", "source_log"),
}
ROWS = {
    5: [dict(n=1000, bits=24, method="SORT", insert_ops_s="123.456789012345",
             query_ops_s=456, memory_kib=789, status="ok", source_log="studies/index.log")],
    6: [dict(dataset="com-lj.ungraph", art_sort_insert_ratio="1.23456789012345",
             art_sort_delete_ratio=2, art_sort_memory_ratio=3, no_chain_two_hop_ratio=4,
             no_chain_bfs_ratio=5, no_chain_sssp_ratio=6, no_chain_bc_ratio=7,
             status="ok", source_logs="studies/ablation.log")],
    7: [dict(dataset="com-lj.ungraph", method="RadixGraph", batch_size=10,
             insert_ops_s="123.456789012345", delete_ops_s=456, memory_gib=1.23,
             status="ok", source_log="studies/batch.log")],
}


class TableFigureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tables = self.root / "tables"
        self.tables.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def write(self, number, rows=None):
        path = self.tables / f"table{number}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS[number])
            writer.writeheader()
            writer.writerows(ROWS[number] if rows is None else rows)
        return path

    def test_merged_cells_place_labels_and_preserve_semantic_formatting(self):
        model, = parse_html_tables(TABLE)
        self.assertEqual((model.rows, model.columns, model.header_rows), (3, 3, 2))
        self.assertEqual([(cell.text, cell.row, cell.column, cell.rowspan, cell.colspan)
                          for cell in model.cells],
                         [("Graphs", 0, 0, 2, 1), ("Results", 0, 1, 1, 2),
                          ("u = 2²⁴", 1, 1, 1, 1), ("Method", 1, 2, 1, 1),
                          ("Graph & <x>", 2, 0, 1, 1), ("1.23×", 2, 1, 1, 1),
                          ("Insert\nthroughput", 2, 2, 1, 1)])
        self.assertTrue(model.cells[5].bold)
        self.assertFalse(model.cells[6].bold)
        self.assertTrue(model.cells[4].header)

    def test_invalid_spans_and_unclosed_tables_are_rejected(self):
        for html in ('<table><tr><td rowspan="zero">x</td></tr></table>',
                     '<table><tr><td rowspan="2">x</td></tr></table>',
                     '<table><tr><td>x</td><td>y</td></tr><tr><td>z</td></tr></table>',
                     '<table><tr><td>x</td></tr>'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                parse_html_tables(html)

    def test_exports_only_vector_pdf_without_changing_source_files(self):
        for number in (5, 6, 7):
            self.write(number)
        originals = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in self.tables.iterdir()}
        result = render_table_figures(self.root)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["figures"], [f"figures/table{number}.pdf" for number in (5, 6, 7)])
        self.assertEqual([item["status"] for item in result["tables"]], ["ok"] * 3)
        self.assertEqual([item["source"] for item in result["tables"]],
                         [f"tables/table{number}.csv" for number in (5, 6, 7)])
        for number in (5, 6, 7):
            pdf = self.root / "figures" / f"table{number}.pdf"
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes())), 1)
            self.assertNotIn(b"/Title", pdf.read_bytes())
        self.assertFalse(list((self.root / "figures").glob("*.png")))
        if shutil.which("pdftotext"):
            extracted = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
            cells = parse_html_tables(format_paper_tables(self.tables)["table7"])[0].cells
            self.assertCountEqual(extracted.split(), " ".join(cell.text for cell in cells).split())
        self.assertEqual(originals, {path: hashlib.sha256(path.read_bytes()).hexdigest()
                                     for path in self.tables.iterdir()})
        self.assertFalse(list(self.tables.glob("*.md")))

    def test_multipage_pdf_utility_keeps_pages_and_removes_legacy_pngs(self):
        figures = self.root / "figures"
        figures.mkdir()
        for name in ("table5.png", "table5_additional.png", "table5_additional2.png"):
            (figures / name).write_bytes(b"legacy PNG")
        models = parse_html_tables(TABLE + TABLE)
        paths = _render_pages(figures, "table5", models)
        self.assertEqual(paths, [figures / "table5.pdf"])
        pdf = (self.root / "figures" / "table5.pdf").read_bytes()
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", pdf)), 2)
        self.assertFalse(list(figures.glob("*.png")))
        _render_pages(figures, "table5", models[:1])
        pdf = (figures / "table5.pdf").read_bytes()
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", pdf)), 1)

    def test_table7_six_dataset_csv_exports_one_pdf_page_with_only_three_paper_datasets(self):
        path = self.tables / "table7.csv"
        fields = ("dataset", "method", "batch_size", "insert_ops_s", "delete_ops_s", "memory_gib", "status", "source_log")
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for dataset in ("uniform-24", "com-orkut.ungraph", "dota-league",
                            "twitter-2010", "graph500-24", "com-lj.ungraph"):
                for method in ("RadixGraph", "Terrace", "Aspen", "CPAM"):
                    for batch in (10, 100, 1000, 10000):
                        writer.writerow(dict(dataset=dataset, method=method, batch_size=batch,
                                             insert_ops_s="1234.567890123456", delete_ops_s="9876.543210987654",
                                             memory_gib="1.23456789", status="ok", source_log=f"logs/{dataset}.log"))
        original = path.read_bytes()
        markdown = format_paper_tables(self.tables)["table7"]
        model, = parse_html_tables(markdown)
        self.assertEqual(model.columns, 14)
        dataset_cells = [cell.text for cell in model.cells if cell.row == 0 and cell.colspan == 4]
        self.assertEqual(dataset_cells, ["LJ", "Orkut", "Twitter"])
        drawn_text = []

        def draw(table):
            figure = _draw_table(table)
            drawn_text.extend(text.get_text() for text in figure.axes[0].texts)
            return figure

        with patch("scripts.table_figures._draw_table", side_effect=draw):
            result = render_table_figures(self.root)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["figures"], ["figures/table7.pdf"])
        self.assertEqual(result["tables"][2]["pages"], 1)
        pdf = self.root / "figures" / "table7.pdf"
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes())), 1)
        self.assertEqual([label for label in drawn_text if label in {"LJ", "Orkut", "Twitter"}], dataset_cells)
        for excluded in ("Dota", "G24", "U24", "dota-league", "graph500-24", "uniform-24"):
            self.assertNotIn(excluded, " ".join(drawn_text))
        if shutil.which("pdftotext"):
            extracted = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
            for label in dataset_cells:
                self.assertEqual(extracted.split().count(label), 1)
            for excluded in ("Dota", "G24", "U24", "dota-league", "graph500-24", "uniform-24"):
                self.assertNotIn(excluded, extracted)
        self.assertFalse(list((self.root / "figures").glob("*.png")))
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.tables.glob("*.md")))

    def test_missing_or_empty_table_removes_only_exporter_owned_stale_files(self):
        figures = self.root / "figures"
        figures.mkdir()
        owned = [f"table{number}{suffix}" for number in (5, 6, 7)
                 for suffix in (".pdf", ".png", "_additional.png", "_additional2.png")]
        preserved = ("figure8.pdf", "table5_custom.pdf", "unrelated.png")
        for name in (*owned, *preserved):
            (figures / name).write_bytes(b"existing artifact")
        self.write(5, [])
        for number in (5, 6, 7):
            (self.tables / f"table{number}.md").write_text("legacy generated Markdown")
        provenance = self.tables / "table7_baselines_notes.md"
        provenance.write_text("Keep the original measurement protocol.")
        result = render_table_figures(self.root)
        self.assertEqual(result["figures"], [])
        self.assertEqual(result["issues"], [])
        self.assertTrue(all(item["status"] == "skipped" for item in result["tables"]))
        self.assertEqual(sorted(path.name for path in figures.iterdir()), sorted(preserved))
        self.assertEqual([path.name for path in self.tables.glob("*.md")], [provenance.name])
        self.assertEqual(provenance.read_text(), "Keep the original measurement protocol.")

    def test_invalid_table_does_not_stop_other_exports_or_leave_stale_output(self):
        self.write(5)
        self.write(6)
        for number in (5, 6, 7):
            (self.tables / f"table{number}.md").write_text("legacy generated Markdown")
        figures = self.root / "figures"
        figures.mkdir()
        (figures / "table5.pdf").write_bytes(b"stale")
        (figures / "table5.png").write_bytes(b"legacy PNG")
        (figures / "table5_additional.png").write_bytes(b"legacy PNG")
        formatted = {"table5": '<table><tr><th rowspan="2">broken</th></tr></table>',
                     "table6": TABLE, "table7": "No measurements available."}
        with patch("scripts.table_figures.format_paper_tables", return_value=formatted):
            result = render_table_figures(self.root)
        self.assertEqual([row["status"] for row in result["tables"]], ["failed", "ok", "skipped"])
        self.assertEqual([issue["artifact"] for issue in result["issues"]], ["table5"])
        self.assertFalse((figures / "table5.pdf").exists())
        self.assertTrue((figures / "table6.pdf").exists())
        self.assertFalse(list(figures.glob("*.png")))
        self.assertFalse(list(figures.glob(".table*")))
        self.assertFalse(list(self.tables.glob("*.md")))

    def test_only_table_cells_are_drawn_with_compact_margins_and_preserved_bolding(self):
        model, = parse_html_tables(TABLE)
        figure = _draw_table(model)
        self.addCleanup(figure.clear)
        texts = {text.get_text(): text for text in figure.axes[0].texts}
        self.assertEqual(list(texts), [cell.text for cell in model.cells])
        self.assertFalse(figure.texts)
        self.assertIn("u = 2²⁴", texts)
        self.assertEqual(texts["1.23×"].get_weight(), "bold")
        self.assertEqual(texts["Insert\nthroughput"].get_weight(), "normal")
        self.assertTrue(all(not text.get_parse_math() for text in texts.values()))
        axes = figure.axes[0]
        self.assertAlmostEqual(min(patch.get_x() for patch in axes.patches), 8)
        self.assertAlmostEqual(min(patch.get_y() for patch in axes.patches), 8)
        self.assertAlmostEqual(axes.get_xlim()[1] - max(patch.get_x() + patch.get_width() for patch in axes.patches), 8)
        self.assertAlmostEqual(axes.get_ylim()[1] - max(patch.get_y() + patch.get_height() for patch in axes.patches), 8)

    def test_smoke_and_paper_profiles_both_export_only_table_cells(self):
        self.write(5)
        expected = [cell.text for cell in parse_html_tables(format_paper_tables(self.tables)["table5"])[0].cells]
        for profile in ("smoke", "paper"):
            with self.subTest(profile=profile):
                (self.root / "manifest.json").write_text(json.dumps({"profile": profile}))
                drawn_text = []

                def draw(model):
                    figure = _draw_table(model)
                    drawn_text.extend(text.get_text() for text in figure.axes[0].texts)
                    return figure

                with patch("scripts.table_figures._draw_table", side_effect=draw):
                    report = render_table_figures(self.root)
                self.assertEqual(report["issues"], [])
                self.assertEqual(report["figures"], ["figures/table5.pdf"])
                self.assertEqual(drawn_text, expected)
                self.assertFalse(list((self.root / "figures").glob("*.png")))

    def test_successful_rerun_removes_legacy_markdown_and_preserves_csv_and_provenance(self):
        for number in (5, 6, 7):
            self.write(number)
            (self.tables / f"table{number}.md").write_text("legacy generated Markdown")
        provenance = self.tables / "table7_baselines_notes.md"
        provenance.write_text("Original measurement details.")
        originals = {path: path.read_bytes() for path in self.tables.glob("*.csv")}
        for _ in range(2):
            report = render_table_figures(self.root)
            self.assertEqual(report["issues"], [])
        self.assertEqual([path.name for path in self.tables.glob("*.md")], [provenance.name])
        self.assertEqual(provenance.read_text(), "Original measurement details.")
        self.assertEqual(originals, {path: path.read_bytes() for path in self.tables.glob("*.csv")})

    def test_corrupt_csv_failure_is_isolated_and_removes_only_obsolete_exports(self):
        corrupt = self.tables / "table5.csv"
        corrupt.write_bytes(b"\xffinvalid UTF-8 measurement\n")
        self.write(6)
        originals = {path: path.read_bytes() for path in self.tables.glob("*.csv")}
        for number in (5, 6, 7):
            (self.tables / f"table{number}.md").write_text("legacy generated Markdown")
        provenance = self.tables / "table7_baselines_notes.md"
        provenance.write_text("Original protocol.")
        figures = self.root / "figures"
        figures.mkdir()
        for name in ("table5.pdf", "table5.png", "table5_additional.png", "figure8.pdf"):
            (figures / name).write_bytes(b"existing artifact")
        report = render_table_figures(self.root)
        self.assertEqual([row["status"] for row in report["tables"]], ["failed", "ok", "skipped"])
        self.assertEqual([row["artifact"] for row in report["issues"]], ["table5"])
        self.assertEqual(report["figures"], ["figures/table6.pdf"])
        self.assertEqual(sorted(path.name for path in figures.iterdir()), ["figure8.pdf", "table6.pdf"])
        self.assertEqual((figures / "figure8.pdf").read_bytes(), b"existing artifact")
        self.assertEqual([path.name for path in self.tables.glob("*.md")], [provenance.name])
        self.assertEqual(provenance.read_text(), "Original protocol.")
        self.assertEqual(originals, {path: path.read_bytes() for path in self.tables.glob("*.csv")})

    def test_cli_exports_figures_from_saved_run(self):
        self.write(6)
        script = Path(__file__).resolve().parents[1] / "scripts" / "table_figures.py"
        result = subprocess.run([sys.executable, str(script), str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["figures"], ["figures/table6.pdf"])


if __name__ == "__main__":
    unittest.main()
