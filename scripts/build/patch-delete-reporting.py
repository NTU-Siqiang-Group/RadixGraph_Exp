#!/usr/bin/env python3
"""Avoid undefined final-edge-normalized milestones for GFE delete-all runs."""
from pathlib import Path
import sys


OLD = "            int aging_coeff = (static_cast<double>(num_ops_done) / m_master.num_edges_final_graph()) * reports_per_ops;"
NEW = """            // Delete-all ends with zero edges, so final-edge-normalized milestones
            // are undefined. Keep operation progress and memory reporting above.
            const uint64_t num_edges_final = m_master.num_edges_final_graph();
            int aging_coeff = num_edges_final == 0 ? 0 :
                (static_cast<double>(num_ops_done) / num_edges_final) * reports_per_ops;"""


def patch_delete_reporting(root):
    path = Path(root) / "experiment/details/aging2_worker.cpp"
    try:
        original = path.read_text()
    except OSError as error:
        raise SystemExit(f"Cannot read GFE deletion reporting source {path}: {error}") from error
    if original.count(OLD) == 0 and original.count(NEW) == 1:
        return
    if original.count(OLD) != 1 or original.count(NEW) != 0:
        raise SystemExit(f"GFE deletion reporting changed in {path}; inspect milestone calculation before building")
    path.write_text(original.replace(OLD, NEW))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch-delete-reporting.py GFE_DIR")
    patch_delete_reporting(Path(sys.argv[1]))
