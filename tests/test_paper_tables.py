import csv
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from scripts.paper_tables import format_paper_tables


FIELDS = {
    5: ("n", "bits", "method", "insert_ops_s", "query_ops_s", "memory_kib", "status", "source_log"),
    6: ("dataset", "art_sort_insert_ratio", "art_sort_delete_ratio", "art_sort_memory_ratio",
        "no_chain_two_hop_ratio", "no_chain_bfs_ratio", "no_chain_sssp_ratio", "no_chain_bc_ratio", "status", "source_logs"),
    7: ("dataset", "method", "batch_size", "insert_ops_s", "delete_ops_s", "memory_gib", "status", "source_log"),
}


def html_rows(markdown, body=True):
    if body:
        sections = re.findall(r"<tbody>(.*?)</tbody>", markdown, re.S)
        markdown = "\n".join(sections)
    return [[cell[1] for cell in re.findall(r"<(th|td)\b[^>]*>(.*?)</\1>", row, re.S)]
            for row in re.findall(r"<tr>(.*?)</tr>", markdown, re.S)]


class PaperTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tables = self.root / "tables"
        self.tables.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def write(self, number, rows):
        path = self.tables / f"table{number}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS[number])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def render(self, number):
        formatted = format_paper_tables(self.tables)
        self.assertFalse(list(self.tables.glob("table[567].md")))
        return formatted[f"table{number}"]

    def test_table5_pivots_metrics_bits_methods_and_bolds_actual_winners(self):
        self.write(5, [
            dict(n="1e3", bits="24.0", method="SORT", insert_ops_s="250000", query_ops_s="9e6", memory_kib="1000", status="ok"),
            dict(n=1000, bits=24, method="ART", insert_ops_s="1.2e5", query_ops_s="1e7", memory_kib="2000", status="ok"),
            dict(n=1000, bits=32, method="SORT", insert_ops_s="300000", query_ops_s="4e7", memory_kib="4000", status="ok"),
            dict(n=1000, bits=32, method="ART", insert_ops_s="400000", query_ops_s="3e7", memory_kib="3000", status="ok"),
        ])
        text = self.render(5)
        self.assertIn('<th rowspan="3">n</th>', text)
        self.assertIn('<th colspan="4">Memory (KB)</th>', text)
        self.assertEqual(text.count('<th colspan="2">u = 2<sup>24</sup></th>'), 3)
        rows = html_rows(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ["10<sup>3</sup>",
                                  "<strong>2.5E5</strong>", "1.2E5", "3.0E5", "<strong>4.0E5</strong>",
                                  "9.0E6", "<strong>1.0E7</strong>", "<strong>4.0E7</strong>", "3.0E7",
                                  "<strong>1.0E3</strong>", "2.0E3", "4.0E3", "<strong>3.0E3</strong>"])
        self.assertIn("KiB", text)

    def test_table5_sorted_actual_n_only_and_failed_or_nonfinite_cells_unavailable(self):
        self.write(5, [
            dict(n=10000, bits=24, method="ART", insert_ops_s="99999999", query_ops_s="99999999", memory_kib="0", status="failed"),
            dict(n=10000, bits=24, method="SORT", insert_ops_s="12", query_ops_s="nan", memory_kib="inf", status="ok"),
            dict(n=1234, bits=24, method="SORT", insert_ops_s="-1", query_ops_s="5", memory_kib="8", status="parse_error"),
        ])
        rows = html_rows(self.render(5))
        self.assertEqual([row[0] for row in rows], ["1234", "10<sup>4</sup>"])
        self.assertEqual(rows[0][1:], ["—"] * 12)
        self.assertEqual(rows[1][1:5], ["1.2E1", "—", "—", "—"])
        self.assertNotIn("<strong>", "".join(rows[1]))
        self.assertEqual(rows[1][5:], ["—"] * 8)

    def test_table5_unsupported_configurations_are_explicitly_retained_in_csv(self):
        self.write(5, [dict(n=100, bits=64, method="Custom", insert_ops_s=5, status="ok")])
        text = self.render(5)
        self.assertIn("No measured rows are available", text)
        self.assertIn("Rows outside the paper's SORT/ART and 24/32-bit configurations", text)
        self.assertIn("[table5.csv](table5.csv)", text)

    def test_table6_alias_order_precision_and_incomplete_rows_keep_valid_ratios(self):
        datasets = ("twitter-2010", "uniform-24", "graph500-24", "dota-league", "com-orkut.ungraph", "com-lj.ungraph")
        self.write(6, [dict(dataset=dataset, art_sort_insert_ratio="1.23456",
                            art_sort_delete_ratio="NaN", art_sort_memory_ratio="0.125",
                            no_chain_two_hop_ratio="2", no_chain_bfs_ratio="",
                            no_chain_sssp_ratio="inf", no_chain_bc_ratio="3.987", status="incomplete")
                       for dataset in datasets])
        text = self.render(6)
        self.assertIn('<th colspan="3">ART v.s. SORT</th>', text)
        self.assertIn('<th colspan="4">Slowdown w/o edge chain</th>', text)
        rows = html_rows(text)
        self.assertEqual([row[0] for row in rows], ["lj", "orkut", "dota", "g24", "u24", "twitter"])
        self.assertTrue(all(row[1:] == ["1.23×", "—", "0.12×", "2.00×", "—", "—", "3.99×"] for row in rows))
        self.assertNotIn("<strong>", text)
        self.assertIn("table6_updates.csv", text)
        self.assertIn("table6_analytics.csv", text)

    def test_table6_failed_row_ratios_are_unavailable_and_labels_escaped(self):
        self.write(6, [dict(dataset='<Graph&"name>', art_sort_insert_ratio="2.5", status="failed")])
        text = self.render(6)
        self.assertIn("&lt;Graph&amp;&quot;name&gt;", text)
        self.assertEqual(html_rows(text)[0][1:], ["—"] * 7)

    def test_table7_layout_mapping_bolding_and_two_decimal_units(self):
        rows = []
        for dataset, scale in (("com-lj.ungraph", 1), ("com-orkut.ungraph", 10), ("twitter-2010", 100)):
            for method, factor, memory in (("RadixGraph", 4, 1.23), ("Terrace", 3, 2.34), ("Aspen", 2, 3.45), ("CPAM", 1, 4.56)):
                for batch in (10, 100, 1000, 10000):
                    rows.append(dict(dataset=dataset, method=method, batch_size=batch,
                                     insert_ops_s=batch * scale * factor,
                                     delete_ops_s=batch * scale * (5 - factor), memory_gib=memory,
                                     status="ok"))
        self.write(7, rows)
        text = self.render(7)
        self.assertIn('<th rowspan="2"></th><th rowspan="2">Batch</th>', text)
        self.assertIn('<th colspan="4">LJ</th><th colspan="4">Orkut</th><th colspan="4">Twitter</th>', text)
        self.assertIn('<th rowspan="4">Insertion<br>throughput</th>', text)
        self.assertIn('<th rowspan="4">Deletion<br>throughput</th>', text)
        body = html_rows(text)
        self.assertEqual(len(body), 9)
        self.assertEqual(body[0][0:6], ["Insertion<br>throughput", "10", "<strong>4.00E1</strong>", "3.00E1", "2.00E1", "1.00E1"])
        self.assertEqual(body[0][6:10], ["<strong>4.00E2</strong>", "3.00E2", "2.00E2", "1.00E2"])
        self.assertEqual(body[1][0], "10<sup>2</sup>")
        self.assertEqual(body[2][0], "10<sup>3</sup>")
        self.assertEqual(body[3][0], "10<sup>4</sup>")
        self.assertEqual(body[4][2:6], ["1.00E1", "2.00E1", "3.00E1", "<strong>4.00E1</strong>"])
        self.assertEqual(body[-1][0:6], ["Memory", "/", "<strong>1.23G</strong>", "2.34G", "3.45G", "4.56G"])
        self.assertIn("GiB", text)
        self.assertIn("measured graph-load RSS delta", text)

    def test_table7_failed_cells_and_lone_available_value_are_not_winners(self):
        self.write(7, [
            dict(dataset="lj", method="RadixGraph", batch_size=10, insert_ops_s="1234.567", delete_ops_s="inf", memory_gib="1.23456", status="ok"),
            dict(dataset="lj", method="Terrace", batch_size=10, insert_ops_s="9999999", delete_ops_s="9999999", memory_gib="0", status="timeout"),
        ])
        text = self.render(7)
        rows = html_rows(text)
        self.assertEqual(rows[0][2:6], ["1.23E3", "—", "—", "—"])
        self.assertEqual(rows[4][2:6], ["—"] * 4)
        self.assertEqual(rows[-1][2:6], ["1.23G", "—", "—", "—"])
        self.assertNotIn("<strong>", text)

    def test_table7_memory_conflict_is_unavailable_but_partial_consistent_success_is_valid(self):
        self.write(7, [
            dict(dataset="lj", method="RadixGraph", batch_size=10, memory_gib="1.1", status="ok"),
            dict(dataset="lj", method="RadixGraph", batch_size=100, memory_gib="1.2", status="ok"),
            dict(dataset="lj", method="Terrace", batch_size=10, memory_gib="2.5", status="ok"),
            dict(dataset="lj", method="Terrace", batch_size=100, memory_gib="3.5", status="failed"),
        ])
        text = self.render(7)
        self.assertEqual(html_rows(text)[-1][2:6], ["—", "2.50G", "—", "—"])
        self.assertIn("Memory differs across recorded batch sizes or successful rows for LJ/RadixGraph", text)
        self.assertIn("[table7.csv](table7.csv) for the individual values", text)

    def test_table7_all_six_datasets_keep_only_paper_datasets_in_paper_order(self):
        rows = [dict(dataset=dataset, method="RadixGraph", batch_size=10,
                     insert_ops_s=str(value), memory_gib="1", status="ok")
                for dataset, value in (("twitter-2010", 300), ("uniform-24", 600),
                                       ("/data/com-orkut.ungraph.e", 200), ("graph500-24", 500),
                                       ("COM-LJ.UNGRAPH", 100), ("dota-league", 400))]
        rows.append(dict(dataset="dota-league", method="ExcludedDatasetMethod", batch_size=10,
                         insert_ops_s="9999", memory_gib="1", status="ok"))
        path = self.write(7, rows)
        before = path.read_bytes()
        text = self.render(7)
        self.assertEqual(text.count("<table>"), 1)
        self.assertIn('<th colspan="4">LJ</th><th colspan="4">Orkut</th><th colspan="4">Twitter</th>', text)
        self.assertNotIn("Additional measured datasets", text)
        self.assertNotIn("ExcludedDatasetMethod", text)
        for excluded in ("Dota", "G24", "U24", "dota-league", "graph500-24", "uniform-24"):
            self.assertNotIn(excluded, text)
        first = html_rows(text)[0]
        self.assertEqual(len(first), 14)
        self.assertEqual([first[index] for index in (2, 6, 10)], ["1.00E2", "2.00E2", "3.00E2"])
        self.assertEqual(path.read_bytes(), before)

    def test_table7_unknown_methods_on_allowed_datasets_remain_visible_and_escaped(self):
        self.write(7, [dict(dataset="lj", method=method, batch_size=10,
                            insert_ops_s="100", memory_gib="1", status="ok")
                       for method in ("RadixGraph", '<New&"Method>')])
        provenance = self.tables / "table7_baselines_notes.md"
        provenance.write_text("Measured protocol")
        original = {path.name: path.read_bytes() for path in self.tables.iterdir()}
        text = self.render(7)
        self.assertEqual(text.count("<table>"), 1)
        self.assertIn('<th colspan="5">LJ</th><th colspan="5">Orkut</th><th colspan="5">Twitter</th>', text)
        self.assertIn("&lt;New&amp;&quot;Method&gt;", text)
        self.assertIn("Additional measured methods are appended", text)
        self.assertIn("table7_baselines_notes.md", text)
        self.assertIn("<strong>1.00E2</strong>", text)
        self.assertEqual({path.name: path.read_bytes() for path in self.tables.iterdir()}, original)

    def test_table7_excluded_only_rows_produce_no_table_and_preserve_csv_bytes(self):
        path = self.write(7, [dict(dataset=dataset, method="ExcludedDatasetMethod", batch_size=10,
                                  insert_ops_s="1234.567890123456", memory_gib="1.23456789", status="ok")
                             for dataset in ("dota-league", "graph500-24", "uniform-24")])
        before = path.read_bytes()
        text = self.render(7)
        self.assertNotIn("<table>", text)
        self.assertIn("No measured", text)
        self.assertNotIn("ExcludedDatasetMethod", text)
        self.assertEqual(path.read_bytes(), before)
        with path.open(newline="") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 3)

    def test_csv_bytes_unchanged_and_missing_or_empty_inputs_are_graceful(self):
        self.write(5, [dict(n=1000, bits=24, method="SORT", insert_ops_s="1234.567890123456", status="ok")])
        self.write(6, [])
        self.write(7, [])
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.tables.iterdir()}
        for _ in range(2):
            formatted = format_paper_tables(self.tables)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.tables.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(set(formatted), {"table5", "table6", "table7"})
        self.assertIn("No measured rows are available", formatted["table6"])
        self.assertFalse(list(self.tables.glob("*.md")))
        missing = self.root / "missing"
        empty = format_paper_tables(missing)
        self.assertEqual(set(empty), {"table5", "table6", "table7"})
        self.assertTrue(all("No measured rows are available" in text for text in empty.values()))
        self.assertFalse(missing.exists())

    def test_cli_exports_pdf_without_markdown_or_changing_csv(self):
        path = self.write(7, [dict(dataset="lj", method="RadixGraph", batch_size=10, insert_ops_s="1234", status="ok")])
        before = path.read_bytes()
        script = Path(__file__).resolve().parents[1] / "scripts" / "paper_tables.py"
        result = subprocess.run([sys.executable, str(script), str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        pdf = self.root / "figures" / "table7.pdf"
        self.assertEqual(result.stdout.strip(), str(pdf))
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
        self.assertFalse(list(self.tables.glob("*.md")))
        self.assertEqual(path.read_bytes(), before)

    def test_selected_table_ignores_unrelated_corrupt_csv_without_creating_files(self):
        self.write(6, [dict(dataset="lj", art_sort_insert_ratio="1.23456", status="incomplete")])
        for number in (5, 7):
            (self.tables / f"table{number}.csv").write_bytes(b"\xffinvalid UTF-8")
        original = {path.name: path.read_bytes() for path in self.tables.iterdir()}
        formatted = format_paper_tables(self.tables, numbers=(6,))
        self.assertEqual(set(formatted), {"table6"})
        self.assertIn("1.23×", formatted["table6"])
        self.assertEqual({path.name: path.read_bytes() for path in self.tables.iterdir()}, original)


if __name__ == "__main__":
    unittest.main()
