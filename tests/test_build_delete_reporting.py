import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts/build/patch-delete-reporting.py"
spec = importlib.util.spec_from_file_location("patch_delete_reporting", MODULE)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

# Pinned upstream reporting block, including the cast and array write that
# previously crashed on ARM64 when delete-all changed the final edge count to 0.
REPORTING = """
            int aging_coeff = (static_cast<double>(num_ops_done) / m_master.num_edges_final_graph()) * reports_per_ops;
            if(aging_coeff > lastset_coeff){
                if( m_master.m_last_time_reported.compare_exchange_strong(/* updates lastset_coeff */ lastset_coeff, aging_coeff) ){
                    uint64_t duration = chrono::duration_cast<chrono::microseconds>(chrono::steady_clock::now() - m_master.m_time_start ).count();
                    m_master.m_reported_times[aging_coeff -1] = duration;
                }
            }
"""


class DeleteReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = self.root / "experiment/details/aging2_worker.cpp"
        self.worker.parent.mkdir(parents=True)
        self.worker.write_text(REPORTING)

    def test_patch_is_idempotent(self):
        patcher.patch_delete_reporting(self.root)
        patched = self.worker.read_text(), self.worker.stat().st_mtime_ns
        patcher.patch_delete_reporting(self.root)
        self.assertEqual(patched, (self.worker.read_text(), self.worker.stat().st_mtime_ns))

    def test_changed_or_duplicate_upstream_is_rejected_without_writes(self):
        for text in (REPORTING.replace("int aging_coeff", "auto aging_coeff"),
                     REPORTING * 2, REPORTING + patcher.NEW):
            with self.subTest(source=text):
                self.worker.write_text(text)
                with self.assertRaisesRegex(SystemExit, "GFE deletion reporting changed"):
                    patcher.patch_delete_reporting(self.root)
                self.assertEqual(self.worker.read_text(), text)

    @unittest.skipUnless(shutil.which("g++"), "g++ is needed to validate patched C++ semantics")
    def test_delete_all_has_no_normalized_reports_and_regular_reports_are_preserved(self):
        patcher.patch_delete_reporting(self.root)
        source = self.root / "reporting.cpp"
        source.write_text("""
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <limits>
using namespace std;
struct Master {
    uint64_t final_edges;
    atomic<int> m_last_time_reported{0};
    chrono::steady_clock::time_point m_time_start = chrono::steady_clock::now() - chrono::seconds(1);
    array<uint64_t, 3> m_reported_times{};
    uint64_t num_edges_final_graph() const { return final_edges; }
};
struct Worker {
    Master& m_master;
    int lastset_coeff = 0;
    void report(uint64_t num_ops_done, double reports_per_ops = 1) {
""" + self.worker.read_text() + """
    }
};
int main() {
    Master deletion{0};
    Worker worker{deletion};
    for (auto count : {uint64_t(0), uint64_t(1024), uint64_t(2048), numeric_limits<uint64_t>::max()}) {
        worker.report(count);
    }
    if (deletion.final_edges != 0 || deletion.m_last_time_reported != 0) return 1;
    for (auto duration : deletion.m_reported_times) if (duration != 0) return 2;

    Master mixed{1024};
    Worker mixed_worker{mixed};
    mixed_worker.report(0);
    if (mixed.m_last_time_reported != 0) return 3;
    mixed_worker.report(1024);
    mixed_worker.report(2048);
    mixed_worker.report(2048);
    if (mixed.m_last_time_reported != 2) return 4;
    if (mixed.m_reported_times[0] == 0 || mixed.m_reported_times[1] == 0) return 5;
    if (mixed.m_reported_times[2] != 0 || mixed.final_edges != 1024) return 6;

    Master scaled{1024};
    Worker scaled_worker{scaled};
    scaled_worker.report(512, 2);
    if (scaled.m_last_time_reported != 1 || scaled.m_reported_times[0] == 0) return 7;
}
""")
        binary = self.root / "reporting"
        compiled = subprocess.run(
            ["g++", "-std=c++17", "-O2", "-fsanitize=undefined,float-cast-overflow",
             "-fno-sanitize-recover=all", str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
