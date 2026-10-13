import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from progress import ProgressBar


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.clock = FakeClock()

    def bar(self, total, **kwargs):
        return ProgressBar(total, stream=self.stream, clock=self.clock, **kwargs)

    def test_setup_does_not_advance_jobs_and_completed_jobs_show_percent(self):
        bar = self.bar(4)
        bar.start("configure")
        self.clock.now = 5
        bar.start("figure8-radixgraph")
        self.clock.now = 65
        bar.complete("figure8-radixgraph")
        self.assertEqual(bar.completed, 1)
        line = self.stream.getvalue().splitlines()[-1]
        self.assertIn("[#####---------------]  25% (1/4 jobs)", line)
        self.assertIn("ok: figure8-radixgraph", line)
        self.assertIn("job 00:01:00", line)
        self.assertIn("elapsed 00:01:05", line)

    def test_non_tty_heartbeat_is_throttled_but_transitions_are_immediate(self):
        bar = self.bar(2)
        bar.start("first")
        self.clock.now = 29
        bar.refresh()
        self.assertEqual(len(self.stream.getvalue().splitlines()), 1)
        self.clock.now = 30
        bar.refresh()
        self.assertEqual(len(self.stream.getvalue().splitlines()), 2)
        self.assertIn("job 00:00:30", self.stream.getvalue())
        bar.complete("first")
        bar.start("second")
        self.assertEqual(len(self.stream.getvalue().splitlines()), 4)
        self.assertNotIn("\r", self.stream.getvalue())
        self.assertNotIn("\x1b", self.stream.getvalue())
        self.assertTrue(self.stream.getvalue().endswith("\n"))

    def test_failed_and_timeout_jobs_count_without_looking_successful(self):
        bar = self.bar(3)
        bar.start("broken")
        bar.complete("broken", "failed")
        bar.start("slow")
        bar.complete("slow", "timeout")
        self.assertEqual(bar.completed, 2)
        self.assertEqual(bar.failed, 2)
        last = self.stream.getvalue().splitlines()[-1]
        self.assertIn("66% (2/3 jobs)", last)
        self.assertIn("failed: 2 | timeout: slow", last)

    def test_interrupt_preserves_incomplete_count_and_finish_is_idempotent(self):
        bar = self.bar(4)
        bar.complete("first")
        bar.start("second")
        self.clock.now = 20
        bar.finish("interrupted")
        final = self.stream.getvalue()
        self.assertIn("25% (1/4 jobs) | interrupted | elapsed 00:00:20", final)
        self.assertNotIn("100%", final)
        bar.refresh()
        bar.finish("finished")
        self.assertEqual(self.stream.getvalue(), final)

    def test_terminal_redraw_refresh_clear_and_final_newline(self):
        self.stream = Terminal()
        bar = self.bar(1)
        bar.start("job")
        initial = self.stream.getvalue()
        self.assertTrue(initial.startswith("\r\x1b[2K"))
        self.assertNotIn("\n", initial)
        self.clock.now = .5
        bar.refresh()
        self.assertEqual(self.stream.getvalue(), initial)
        self.clock.now = 1
        bar.refresh()
        self.assertIn("job 00:00:01", self.stream.getvalue())
        bar.clear()
        self.assertTrue(self.stream.getvalue().endswith("\r\x1b[2K"))
        cleared = self.stream.getvalue()
        bar.clear()
        self.assertEqual(self.stream.getvalue(), cleared)
        bar.complete("job")
        bar.finish()
        self.assertIn("100% (1/1 jobs) | finished", self.stream.getvalue())
        self.assertEqual(self.stream.getvalue().count("\n"), 1)

    def test_zero_total_has_a_finite_clean_snapshot(self):
        bar = self.bar(0)
        bar.finish()
        self.assertIn("0% (0/0 jobs) | finished", self.stream.getvalue())
        self.assertTrue(self.stream.getvalue().endswith("\n"))

    def test_completed_job_duration_stops_when_job_finishes(self):
        bar = self.bar(1, interval=1)
        bar.start("job")
        self.clock.now = 5
        bar.complete("job")
        self.clock.now = 20
        bar.refresh()
        self.assertIn("job 00:00:05 | elapsed 00:00:20", self.stream.getvalue().splitlines()[-1])

    def test_omitted_job_does_not_inherit_previous_job_duration(self):
        bar = self.bar(2, interval=1)
        bar.start("long job")
        self.clock.now = 600
        bar.complete("long job")
        bar.complete("missing dataset", "expected_omission")
        self.clock.now = 601
        bar.refresh()
        last = self.stream.getvalue().splitlines()[-1]
        self.assertIn("expected_omission: missing dataset", last)
        self.assertNotIn(" | job ", last)
        self.assertIn("elapsed 00:10:01", last)

    def test_terminal_long_labels_fit_without_wrapping(self):
        self.stream = Terminal()
        with patch("progress.shutil.get_terminal_size", return_value=os.terminal_size((80, 24))):
            bar = self.bar(100)
            bar.start("figure8-" + "long-dataset-and-library-name-" * 5)
        line = self.stream.getvalue().removeprefix("\r\x1b[2K")
        self.assertLessEqual(len(line), 79)
        self.assertIn("...", line)
        self.assertIn("job 00:00:00", line)
        self.assertIn("elapsed 00:00:00", line)

    def test_names_cannot_insert_extra_lines(self):
        bar = self.bar(1)
        bar.start("first\nsecond\rthird")
        self.assertEqual(len(self.stream.getvalue().splitlines()), 1)
        self.assertIn("first second third", self.stream.getvalue())

    def test_invalid_total_and_interval_fail_early(self):
        for total in (-1, 2.5):
            with self.subTest(total=total), self.assertRaises(ValueError):
                self.bar(total)
        with self.assertRaises(ValueError):
            self.bar(1, interval=-1)


if __name__ == "__main__":
    unittest.main()
