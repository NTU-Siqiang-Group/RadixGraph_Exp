#!/usr/bin/env python3
"""Make the pinned GFE driver's internal limits honor zero as unlimited."""
from pathlib import Path
import sys


PATCHES = (
    ("experiment/details/aging2_master.cpp", (
        ("chrono::steady_clock::time_point timeout = now + ( m_parameters.m_timeout == 0s ? /* 1 month */ 31 * 24h : m_parameters.m_timeout );",
         "chrono::steady_clock::time_point timeout = now + m_parameters.m_timeout;"),
        ("if(now >= timeout){",
         "if(m_parameters.m_timeout > 0s && now >= timeout){"),
    )),
    ("library/gtx/gtx_driver.cpp", (
        ("utility::TimeoutService timeout { 172800 }; // timeout: 48h, lcc's complexity is high so we use this to avoid overtime computations",
         "utility::TimeoutService timeout { m_timeout }; // Honor the configured kernel limit (0 => indefinite)."),
    )),
)


def patch_timeouts(root):
    updates = []
    for relative, replacements in PATCHES:
        path = Path(root) / relative
        try:
            original = path.read_text()
        except OSError as error:
            raise SystemExit(f"Cannot read GFE timeout source {path}: {error}") from error
        text = original
        for old, new in replacements:
            if text.count(old) == 0 and text.count(new) == 1:
                continue
            if text.count(old) != 1 or text.count(new) != 0:
                raise SystemExit(f"GFE timeout implementation changed in {path}; inspect internal time limits before building")
            text = text.replace(old, new)
        updates.append((path, original, text))

    # Validate every source before changing any file, so upstream drift fails cleanly.
    for path, original, text in updates:
        if text != original:
            path.write_text(text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch-timeouts.py GFE_DIR")
    patch_timeouts(Path(sys.argv[1]))
