import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "build" / "patch-timeouts.py"
spec = importlib.util.spec_from_file_location("patch_timeouts", MODULE)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

AGING = """
bool aging_timed_out(chrono::seconds budget, chrono::seconds elapsed) {
    struct Parameters { chrono::seconds m_timeout; } m_parameters{budget};
    chrono::steady_clock::time_point now{};
    chrono::steady_clock::time_point timeout = now + ( m_parameters.m_timeout == 0s ? /* 1 month */ 31 * 24h : m_parameters.m_timeout );
    now += elapsed;
    if(now >= timeout){
        return true;
    }
    return false;
}
"""

GTX = """
chrono::seconds lcc_budget(chrono::seconds m_timeout) {
    utility::TimeoutService timeout { 172800 }; // timeout: 48h, lcc's complexity is high so we use this to avoid overtime computations
    return timeout.budget;
}
"""


class BuildTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.aging = self.root / "experiment" / "details" / "aging2_master.cpp"
        self.gtx = self.root / "library" / "gtx" / "gtx_driver.cpp"
        for path, text in ((self.aging, AGING), (self.gtx, GTX)):
            path.parent.mkdir(parents=True)
            path.write_text(text)

    def test_patch_is_idempotent(self):
        patcher.patch_timeouts(self.root)
        patched = {path: (path.read_text(), path.stat().st_mtime_ns)
                   for path in (self.aging, self.gtx)}
        self.assertNotIn("31 * 24h", patched[self.aging][0])
        self.assertNotIn("172800", patched[self.gtx][0])
        patcher.patch_timeouts(self.root)
        self.assertEqual(patched, {path: (path.read_text(), path.stat().st_mtime_ns)
                                  for path in (self.aging, self.gtx)})

    def test_upstream_drift_changes_no_files(self):
        for path, old, new in ((self.aging, "31 * 24h", "30 * 24h"),
                               (self.aging, "now >= timeout", "now > timeout"),
                               (self.gtx, "172800", "86400")):
            with self.subTest(path=path, old=old):
                self.aging.write_text(AGING)
                self.gtx.write_text(GTX)
                path.write_text(path.read_text().replace(old, new))
                before = {p: p.read_text() for p in (self.aging, self.gtx)}
                with self.assertRaisesRegex(SystemExit, "GFE timeout implementation changed"):
                    patcher.patch_timeouts(self.root)
                self.assertEqual(before, {p: p.read_text() for p in before})

    def test_duplicate_or_mixed_patch_is_rejected(self):
        old, new = patcher.PATCHES[1][1][0]
        for extra in (old, new):
            with self.subTest(extra=extra):
                self.gtx.write_text(GTX + "\n" + extra + "\n")
                with self.assertRaisesRegex(SystemExit, "GFE timeout implementation changed"):
                    patcher.patch_timeouts(self.root)
                self.assertEqual(self.aging.read_text(), AGING)

    @unittest.skipUnless(shutil.which("g++"), "g++ is needed to validate patched C++ semantics")
    def test_zero_is_unlimited_and_positive_limits_are_preserved(self):
        patcher.patch_timeouts(self.root)
        source = self.root / "timeout-semantics.cpp"
        source.write_text("""
#include <chrono>
using namespace std;
using namespace std::chrono_literals;
namespace utility {
    struct TimeoutService {
        chrono::seconds budget;
        explicit TimeoutService(chrono::seconds value) : budget(value) {}
    };
}
""" + self.aging.read_text() + self.gtx.read_text() + """
int main() {
    if (aging_timed_out(0s, 0s) || aging_timed_out(0s, 31 * 24h)
        || aging_timed_out(0s, 1000h)) return 1;
    if (aging_timed_out(10s, 9s) || !aging_timed_out(10s, 10s)
        || !aging_timed_out(10s, 11s)) return 2;
    if (lcc_budget(0s) != 0s || lcc_budget(3h) != 3h) return 3;
}
""")
        binary = self.root / "timeout-semantics"
        compiled = subprocess.run(["g++", "-std=c++17", str(source), "-o", str(binary)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
