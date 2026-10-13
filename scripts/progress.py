"""Job progress that remains readable in Docker's default non-TTY output."""

import shutil
import sys
import time


class ProgressBar:
    """Display completed logical jobs, with elapsed time for the active command.

    ``start`` may be called for setup commands without advancing the bar. Call
    ``complete`` once per logical job in ``total``, including failed jobs. A
    non-TTY stream receives occasional newline-separated snapshots; a terminal
    receives a line that is redrawn in place. No background thread is required.
    """

    def __init__(self, total, stream=None, clock=None, interval=None):
        if not isinstance(total, int) or total < 0:
            raise ValueError("total must be a nonnegative integer")
        self.total = total
        self.completed = 0
        self.failed = 0
        self.stream = sys.stderr if stream is None else stream
        self.clock = time.monotonic if clock is None else clock
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.interval = (1.0 if self.tty else 30.0) if interval is None else float(interval)
        if self.interval < 0:
            raise ValueError("interval must be nonnegative")
        self._started = self.clock()
        self._last_rendered = None
        self._current = None
        self._current_started = None
        self._current_elapsed = None
        self._status = "waiting"
        self._line_visible = False
        self._finished = False

    @staticmethod
    def _label(value):
        # Job names and statuses must not break the one-line display.
        return " ".join(str(value).split())

    @staticmethod
    def _duration(seconds):
        minutes, seconds = divmod(max(0, int(seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def start(self, name):
        if self._finished:
            return
        now = self.clock()
        self._current = self._label(name)
        self._current_started = now
        self._current_elapsed = None
        self._status = "running"
        self._render(now, force=True)

    def complete(self, name, status="ok"):
        if self._finished:
            return
        now = self.clock()
        self.completed = min(self.completed + 1, self.total)
        status = self._label(status)
        if status not in ("ok", "success", "skipped", "expected_omission"):
            self.failed += 1
        name = self._label(name)
        self._current_elapsed = (now - self._current_started
                                 if name == self._current and self._current_started is not None
                                 else None)
        self._current = name
        self._current_started = None
        self._status = status
        self._render(now, force=True)

    def refresh(self):
        if not self._finished:
            self._render(self.clock())

    def clear(self):
        """Erase a terminal progress line before printing a separate message."""
        if self.tty and self._line_visible:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()
            self._line_visible = False

    def finish(self, status="finished"):
        """Print a final snapshot without counting unfinished jobs as complete."""
        if self._finished:
            return
        self._status = self._label(status)
        self._current = None
        self._render(self.clock(), force=True, final=True)
        self._finished = True

    def _render(self, now, force=False, final=False):
        if not force and self._last_rendered is not None and now - self._last_rendered < self.interval:
            return
        percent = self.completed * 100 // self.total if self.total else 0
        width = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns - 1) if self.tty else None
        bar_width = 10 if width is not None and width < 100 else 20
        filled = self.completed * bar_width // self.total if self.total else 0
        line = f"[{'#' * filled}{'-' * (bar_width - filled)}] {percent:3d}% ({self.completed}/{self.total} jobs)"
        if self.failed:
            line += f" | failed: {self.failed}"
        label = self._status
        timing = ""
        if self._current is not None:
            label += f": {self._current}"
            elapsed = self._current_elapsed
            if elapsed is None and self._current_started is not None:
                elapsed = now - self._current_started
            if elapsed is not None:
                timing += f" | job {self._duration(elapsed)}"
        timing += f" | elapsed {self._duration(now - self._started)}"
        if width is not None:
            available = max(0, width - len(line) - len(timing) - 3)
            if len(label) > available:
                label = label[:available - 3] + "..." if available >= 3 else label[:available]
        line += " | " + label + timing
        if self.tty:
            if len(line) > width:
                line = line[:width - 3] + "..." if width >= 3 else line[:width]
            self.stream.write("\r\x1b[2K" + line + ("\n" if final else ""))
            self._line_visible = not final
        else:
            self.stream.write(line + "\n")
        self.stream.flush()
        self._last_rendered = now
