#!/usr/bin/env python3
"""Enumerate actual CPU package IDs instead of assuming zero-based IDs."""
from pathlib import Path
import sys


SOURCES = (
    "third-party/libcommon/src/cpu_topology.cpp",
    "graphlog-base/lib/common/src/cpu_topology.cpp",
    "teseo/src/util/cpu_topology.cpp",
)

SIGNATURE = "std::vector<int> cpu_topology::get_threads(bool interleaved, bool include_smt) const {"

OLD = """std::vector<int> cpu_topology::get_threads(bool interleaved, bool include_smt) const {
    std::vector<int> result;

    if (interleaved) {
        std::vector<std::vector<int>> threads;
        for (size_t i = 0; i < get_node_count(); i++) {
            threads.push_back(get_threads_on_node(i, include_smt));
        }

        // check all nodes have the same size
        assert(!threads.empty());
        for (size_t i = 1; i < threads.size(); i++) {
            if (threads[i].size() != threads[0].size()) {
                throw cpu_topology_exception("[cpu_topology::get_threads] nodes with different number of threads?");
            }
        }

        for (size_t j = 0; j < threads[0].size(); j++) {
            for (size_t i = 0; i < get_node_count(); i++) {
                result.push_back(threads[/* node = */i][/* thread = */ j]);
            }
        }
    } else {
        for (size_t i = 0; i < get_node_count(); i++) {
            auto threads = get_threads_on_node(i, include_smt);
            for (size_t j = 0; j < threads.size(); j++) {
                result.push_back(threads[j]);
            }
        }
    }

    return result;
}"""

NEW = """std::vector<int> cpu_topology::get_threads(bool interleaved, bool include_smt) const {
    std::vector<int> result;
    std::vector<int> node_ids;
    get_nodes(node_ids);

    // CPU package IDs from sysfs can be sparse, nonzero, or -1 (unknown).
    // Keep those IDs, and their topology order, when looking up each node.
    if (interleaved) {
        std::vector<std::vector<int>> threads;
        size_t max_threads = 0;
        for (int node_id : node_ids) {
            threads.push_back(get_threads_on_node(node_id, include_smt));
            if (threads.back().size() > max_threads) {
                max_threads = threads.back().size();
            }
        }

        // A VM or heterogeneous machine can expose different core counts.
        // Preserve round-robin order, skipping nodes whose threads ran out.
        for (size_t j = 0; j < max_threads; j++) {
            for (const auto& node_threads : threads) {
                if (j < node_threads.size()) {
                    result.push_back(node_threads[j]);
                }
            }
        }
    } else {
        for (int node_id : node_ids) {
            auto threads = get_threads_on_node(node_id, include_smt);
            result.insert(result.end(), threads.begin(), threads.end());
        }
    }

    return result;
}"""


def patch_cpu_topology(root):
    updates = []
    for relative in SOURCES:
        path = Path(root) / relative
        try:
            original = path.read_text()
        except OSError as error:
            raise SystemExit(f"Cannot read CPU topology source {path}: {error}") from error
        if original.count(SIGNATURE) != 1:
            raise SystemExit(
                f"CPU topology source changed in {path}; inspect get_threads() "
                "and update patch-cpu-topology.py before building")
        if original.count(OLD) == 0 and original.count(NEW) == 1:
            continue
        if original.count(OLD) != 1 or original.count(NEW) != 0:
            raise SystemExit(
                f"CPU topology source changed in {path}; inspect get_threads() "
                "and update patch-cpu-topology.py before building")
        updates.append((path, original.replace(OLD, NEW)))

    # Validate every pinned copy before modifying any of them.
    for path, updated in updates:
        path.write_text(updated)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch-cpu-topology.py GFE_DIR")
    patch_cpu_topology(Path(sys.argv[1]))
