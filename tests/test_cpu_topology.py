from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest


BUILD = Path(__file__).resolve().parents[1] / "scripts/build"
spec = importlib.util.spec_from_file_location("patch_cpu_topology", BUILD / "patch-cpu-topology.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

# The pinned libcommon, graphlog, and Teseo copies contain this same method.
# Keep this fixture independent of the patch's replacement text, so compiling
# the original also demonstrates the reported exception without dependencies.
ORIGINAL_METHOD = """std::vector<int> cpu_topology::get_threads(bool interleaved, bool include_smt) const {
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


def write_sources(root):
    for relative in patcher.SOURCES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// fixture prefix\n" + ORIGINAL_METHOD + "\n// fixture suffix\n")


class CpuTopologyPatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_sources(self.root)

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }

    def test_patch_is_idempotent_without_rewriting_files(self):
        patcher.patch_cpu_topology(self.root)
        # A fixed timestamp makes an accidental rewrite observable on every FS.
        for relative in patcher.SOURCES:
            os.utime(self.root / relative, ns=(1_000_000_000, 1_000_000_000))
        first = self.snapshot()
        patcher.patch_cpu_topology(self.root)
        self.assertEqual(self.snapshot(), first)

    def test_changed_or_duplicate_upstream_is_rejected_without_writes(self):
        path = self.root / patcher.SOURCES[-1]
        original = path.read_text()
        invalid_sources = (
            original.replace("get_threads_on_node(i, include_smt)", "get_threads_on_node(i + 1, include_smt)"),
            original + "\n" + ORIGINAL_METHOD,
            original + "\n" + ORIGINAL_METHOD.replace("return result;", "return {};"),
        )
        for invalid in invalid_sources:
            with self.subTest(source=invalid):
                path.write_text(invalid)
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "CPU topology source changed.*inspect get_threads"):
                    patcher.patch_cpu_topology(self.root)
                self.assertEqual(self.snapshot(), before)

    def test_missing_source_is_rejected_without_writes(self):
        (self.root / patcher.SOURCES[-1]).unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "Cannot read CPU topology source"):
            patcher.patch_cpu_topology(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_changed_previously_patched_source_is_rejected_without_writes(self):
        patcher.patch_cpu_topology(self.root)
        path = self.root / patcher.SOURCES[-1]
        path.write_text(path.read_text().replace("get_nodes(node_ids);", "get_nodes(node_ids); // local change"))
        # The first file still needs the patch, so a late rejection must leave it alone.
        first = self.root / patcher.SOURCES[0]
        first.write_text("// fixture prefix\n" + ORIGINAL_METHOD + "\n// fixture suffix\n")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "CPU topology source changed"):
            patcher.patch_cpu_topology(self.root)
        self.assertEqual(self.snapshot(), before)


HARNESS_PREFIX = r"""
#include <cassert>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using cpu_topology_exception = std::runtime_error;

struct Node {
    int id;
    std::vector<int> physical;
    std::vector<int> smt;
};

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
"""

# Only topology discovery and per-node lookups are faked. The patched
# get_threads() below is read from each actual patch output and compiled.
FAKE_TOPOLOGY = r"""
class cpu_topology {
    std::vector<Node> nodes;
public:
    mutable std::vector<std::pair<int, bool>> lookups;
    explicit cpu_topology(std::vector<Node> input) : nodes(std::move(input)) {}
    size_t get_node_count() const { return nodes.size(); }
    void get_nodes(std::vector<int>& output) const {
        output.clear();
        for (const auto& node : nodes) output.push_back(node.id);
    }
    std::vector<int> get_threads_on_node(int node_id, bool include_smt) const {
        lookups.emplace_back(node_id, include_smt);
        for (const auto& node : nodes) {
            if (node.id == node_id) return include_smt ? node.smt : node.physical;
        }
        throw cpu_topology_exception("[cpu_topology::get_threads_on_node] node does not exist");
    }
    std::vector<int> get_threads(bool interleaved, bool include_smt) const;
};
"""

HARNESS_TESTS = r"""
struct Case {
    std::string name;
    std::vector<Node> nodes;
    std::vector<int> flat_physical;
    std::vector<int> flat_smt;
    std::vector<int> interleaved_physical;
    std::vector<int> interleaved_smt;
};

const std::vector<Case> cases = {
    {"uniform", {{0, {0, 1}, {0, 8, 1, 9}}, {1, {4, 5}, {4, 12, 5, 13}}},
     {0, 1, 4, 5}, {0, 8, 1, 9, 4, 12, 5, 13},
     {0, 4, 1, 5}, {0, 4, 8, 12, 1, 5, 9, 13}},
    {"sparse", {{2, {10, 14}, {10, 11, 14, 15}}, {7, {3, 5}, {3, 4, 5, 6}}},
     {10, 14, 3, 5}, {10, 11, 14, 15, 3, 4, 5, 6},
     {10, 3, 14, 5}, {10, 3, 11, 4, 14, 5, 15, 6}},
    {"nonzero", {{3, {0, 2}, {0, 1, 2, 3}}},
     {0, 2}, {0, 1, 2, 3}, {0, 2}, {0, 1, 2, 3}},
    {"negative", {{-1, {0, 2}, {0, 1, 2, 3}}},
     {0, 2}, {0, 1, 2, 3}, {0, 2}, {0, 1, 2, 3}},
    {"node_order", {{7, {12}, {12, 13}}, {2, {4}, {4, 5}}},
     {12, 4}, {12, 13, 4, 5}, {12, 4}, {12, 4, 13, 5}},
    {"uneven", {{2, {0, 2, 4}, {0, 1, 2, 3, 4, 5}}, {7, {8}, {8, 9}}},
     {0, 2, 4, 8}, {0, 1, 2, 3, 4, 5, 8, 9},
     {0, 8, 2, 4}, {0, 8, 1, 9, 2, 3, 4, 5}},
    {"empty_node", {{-1, {}, {}}, {7, {8}, {8, 9}}},
     {8}, {8, 9}, {8}, {8, 9}},
    {"empty", {}, {}, {}, {}, {}},
    {"all_empty_nodes", {{2, {}, {}}, {7, {}, {}}}, {}, {}, {}, {}},
};

template<class Topology>
static void run_case(const std::string& selected) {
    for (const auto& test : cases) {
        if (test.name != selected) continue;
        for (bool interleaved : {false, true}) {
            for (bool smt : {false, true}) {
                Topology topology(test.nodes);
                const auto actual = topology.get_threads(interleaved, smt);
                const auto& expected = interleaved ?
                    (smt ? test.interleaved_smt : test.interleaved_physical) :
                    (smt ? test.flat_smt : test.flat_physical);
                require(actual == expected, "wrong thread IDs or order");
                std::vector<std::pair<int, bool>> expected_lookups;
                for (const auto& node : test.nodes) expected_lookups.emplace_back(node.id, smt);
                require(topology.lookups == expected_lookups, "wrong node ID or SMT flag in per-node lookup");
            }
        }
        return;
    }
    throw std::runtime_error("unknown test case");
}

static void reproduce_original() {
    for (bool interleaved : {false, true}) {
        for (bool smt : {false, true}) {
            original::cpu_topology topology({{2, {10}, {10, 11}}, {7, {3}, {3, 4}}});
            bool reproduced = false;
            try {
                topology.get_threads(interleaved, smt);
            } catch (const cpu_topology_exception& error) {
                reproduced = std::string(error.what()) ==
                    "[cpu_topology::get_threads_on_node] node does not exist";
            }
            require(reproduced, "original sparse-ID topology did not reproduce the reported exception");
        }
    }
}

int main(int argc, char** argv) {
    try {
        require(argc == 2, "one test case must be selected");
        const std::string selected = argv[1];
        if (selected == "original") {
            reproduce_original();
        } else {
            run_case<patched_0::cpu_topology>(selected);
            run_case<patched_1::cpu_topology>(selected);
            run_case<patched_2::cpu_topology>(selected);
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
"""


@unittest.skipUnless(shutil.which("g++"), "g++ is needed to validate the patched CPU topology method")
class CpuTopologyEnumerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        write_sources(root)
        patcher.patch_cpu_topology(root)
        implementations = [f"namespace original {{\n{FAKE_TOPOLOGY}\n{ORIGINAL_METHOD}\n}}\n"]
        for index, relative in enumerate(patcher.SOURCES):
            implementations.append(
                f"namespace patched_{index} {{\n{FAKE_TOPOLOGY}\n{(root / relative).read_text()}\n}}\n")
        source = root / "cpu_topology.cpp"
        source.write_text(HARNESS_PREFIX + "".join(implementations) + HARNESS_TESTS)
        cls.binary = root / "cpu_topology"
        compiled = subprocess.run(
            ["g++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(cls.binary)],
            capture_output=True, text=True, timeout=30)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)

    def run_case(self, case):
        checked = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_original_sparse_node_ids_reproduce_reported_exception(self):
        self.run_case("original")

    def test_uniform_x64_thread_order_is_preserved(self):
        self.run_case("uniform")

    def test_sparse_node_ids_work(self):
        self.run_case("sparse")

    def test_single_nonzero_node_id_works(self):
        self.run_case("nonzero")

    def test_unknown_negative_package_id_is_preserved(self):
        self.run_case("negative")

    def test_topology_node_order_is_preserved(self):
        self.run_case("node_order")

    def test_uneven_node_sizes_interleave_without_losing_threads(self):
        self.run_case("uneven")

    def test_empty_first_node_is_skipped(self):
        self.run_case("empty_node")

    def test_empty_topology_returns_empty(self):
        self.run_case("empty")

    def test_all_empty_nodes_return_empty(self):
        self.run_case("all_empty_nodes")


if __name__ == "__main__":
    unittest.main()
