import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "batch_baselines.py"
spec = importlib.util.spec_from_file_location("batch_baselines", MODULE)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def complete_log():
    return "verbose upstream output\nBATCH_THREADS 2\n" + "BATCH_MEMORY " + json.dumps(
        dict(metric="graph_load_rss_delta", memory_gib=1.25, rss_before_bytes=1024, rss_after_bytes=1342178304)) + "\n" + "\n".join(
            "BATCH_RESULT " + json.dumps(dict(batch_size=size, trials=2, insert_ops_s=size * 10, delete_ops_s=size * 5))
            for size in batch.BATCH_SIZES)


class BatchBaselineTests(unittest.TestCase):
    def test_truncated_and_nonfinite_measurements_rejected(self):
        self.assertEqual(len(batch.parse_measurements(complete_log())["batches"]), 4)
        with self.assertRaises(ValueError):
            batch.parse_measurements(complete_log().rsplit("\n", 1)[0])
        with self.assertRaises(ValueError):
            batch.parse_measurements(complete_log().replace('"insert_ops_s": 100,', '"insert_ops_s": NaN,'))

    def test_report_retains_missing_rows_without_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            batch._save_records(output, [dict(status="missing", log="", metadata=dict(dataset="LJ", method="Terrace", threads=2))])
            batch.render_batch_reports(output)
            rows = list(csv.DictReader(io.StringIO((output / "tables" / "table7_baselines.csv").read_text())))
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row["status"] == "missing" and row["insert_ops_s"] == "" for row in rows))

    def test_report_detects_lost_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            logfile = output / "run.log"
            logfile.write_text(complete_log())
            record = dict(status="ok", log=str(logfile), metadata=dict(dataset="LJ", method="Aspen", threads=2))
            batch._save_records(output, [record])
            batch.render_batch_reports(output)
            rows = list(csv.DictReader(io.StringIO((output / "tables" / "table7_baselines.csv").read_text())))
            self.assertTrue(all(row["status"] == "ok" and row["memory_gib"] == "1.25" for row in rows))
            logfile.write_text(complete_log().rsplit("\n", 1)[0])
            batch.render_batch_reports(output)
            rows = list(csv.DictReader(io.StringIO((output / "tables" / "table7_baselines.csv").read_text())))
            self.assertTrue(all(row["status"] == "failed" and row["insert_ops_s"] == "" for row in rows))

    def test_report_relocates_container_logs_and_includes_interrupted_job(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "logs").mkdir()
            (output / "logs" / "aspen.log").write_text(complete_log())
            metadata = dict(dataset="LJ", method="Aspen", threads=2, suite="batch_baselines")
            jobs = [dict(name="aspen", status="ok", log="/output/old-run/logs/aspen.log", metadata=metadata),
                    dict(name="cpam", status="interrupted", log="", metadata={**metadata, "method": "CPAM"})]
            (output / "manifest.json").write_text(json.dumps(dict(jobs=jobs)))
            batch._save_records(output, jobs[:1])
            report = batch.render_batch_reports(output)
            rows = list(csv.DictReader(io.StringIO((output / "tables" / "table7_baselines.csv").read_text())))
            self.assertEqual(len(rows), 8)
            self.assertTrue(all(row["status"] == "ok" and row["source_log"] == "logs/aspen.log" for row in rows[:4]))
            self.assertTrue(all(row["status"] == "interrupted" for row in rows[4:]))
            self.assertEqual(report["issues"][0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
