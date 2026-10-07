#!/usr/bin/env python3
"""Restore atomic GAPBS queue operations in the pinned RadixGraph sources."""
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
replacements = (
    ("T orig_val = x;\n  x += inc;\n  return orig_val;",
     "return __atomic_fetch_add(&x, inc, __ATOMIC_SEQ_CST);"),
    ("if (x == old_val) {\n    x = new_val;\n    return true;\n  }\n  return false;",
     "T expected = old_val;\n  T desired = new_val;\n"
     "  return __atomic_compare_exchange(&x, &expected, &desired, false, "
     "__ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);"),
)
for old, new in replacements:
    if text.count(new) == 1:
        continue
    if text.count(old) != 1:
        raise SystemExit(f"Atomic helper changed in {path}; inspect GAPBS synchronization")
    text = text.replace(old, new)
path.write_text(text)
