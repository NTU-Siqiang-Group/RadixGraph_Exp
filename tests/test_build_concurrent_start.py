from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest


BUILD = Path(__file__).resolve().parents[1] / "scripts/build"
spec = importlib.util.spec_from_file_location("patch_concurrent_start", BUILD / "patch-concurrent-start.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class ConcurrentStartPatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative, replacements in patcher.PATCHES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(old for old, _ in replacements))

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }

    def test_patch_is_idempotent_and_installs_actual_helper(self):
        patcher.patch_concurrent_start(self.root)
        header = self.root / "experiment/details/smoke_concurrent_start.hpp"
        self.assertEqual(header.read_bytes(), (BUILD / header.name).read_bytes())
        for relative, replacements in patcher.PATCHES:
            patched = (self.root / relative).read_text()
            for _, new in replacements:
                self.assertEqual(patched.count(new), 1)
        first = self.snapshot()
        patcher.patch_concurrent_start(self.root)
        self.assertEqual(self.snapshot(), first)

    def test_changed_or_duplicate_upstream_is_rejected_without_writes(self):
        relative, replacements = patcher.PATCHES[-1]
        path = self.root / relative
        original = path.read_text()
        old, _ = replacements[-1]
        changed = old.replace("while", "for", 1)
        for invalid in (original.replace(old, changed), original + "\n" + old):
            with self.subTest(source=invalid):
                path.write_text(invalid)
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "GFE concurrent startup changed"):
                    patcher.patch_concurrent_start(self.root)
                self.assertEqual(self.snapshot(), before)

    def test_changed_helper_is_rejected_without_writes(self):
        patcher.patch_concurrent_start(self.root)
        header = self.root / "experiment/details/smoke_concurrent_start.hpp"
        header.write_text(header.read_text() + "\n// unexpected local change\n")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "GFE smoke startup helper changed"):
            patcher.patch_concurrent_start(self.root)
        self.assertEqual(self.snapshot(), before)


# Exercise the actual helper, including its blocking behavior, rather than a
# Python model of the reader/writer handshake. Each case has a subprocess
# timeout so a regression in the unblock paths cannot hang the test runner.
HARNESS = r"""
#include "smoke_concurrent_start.hpp"
#include <atomic>
#include <chrono>
#include <cmath>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using gfe::experiment::details::SmokeConcurrentStart;
using namespace std::chrono_literals;

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

template<class Predicate>
static bool eventually(Predicate predicate) {
    const auto deadline = std::chrono::steady_clock::now() + 3s;
    while (!predicate()) {
        if (std::chrono::steady_clock::now() >= deadline) return false;
        std::this_thread::yield();
    }
    return true;
}

static void disabled() {
    SmokeConcurrentStart start;
    std::atomic<bool> stop{false};
    require(!start.enabled(), "handshake must be disabled by default");
    // These calls must return without a reader, even at the parking threshold.
    start.checkpoint(10, 100, stop);
    start.checkpoint(100, 100, stop);
    require(!start.graph_ready(), "disabled handshake announced readiness");
}

static void delayed_reader() {
    SmokeConcurrentStart start;
    std::atomic<bool> stop{false};
    std::atomic<unsigned> completed{0};
    require(start.enabled(), "explicit smoke handshake was not enabled");
    auto writer = std::async(std::launch::async, [&] {
        completed.store(10);
        start.checkpoint(10, 100, stop);
        // This represents the pending write workload, which must survive the
        // reader-start delay instead of finishing before the first read.
        completed.store(100);
        start.checkpoint(100, 100, stop);
    });
    SmokeConcurrentStart::ReleaseGuard release_on_error(start);
    require(eventually([&] { return start.graph_ready(); }), "writer never announced a usable graph");
    require(completed.load() == 10, "writer consumed pending writes before the reader started");
    require(writer.wait_for(0s) == std::future_status::timeout, "writer did not park for reader startup");
    require(std::abs(start.progress() - 0.1) < 1e-12, "checkpoint published incorrect progress");
    start.release_readers();
    require(writer.wait_for(3s) == std::future_status::ready, "reader release did not unblock writer");
    writer.get();
    require(completed.load() == 100, "pending writes were not completed");
    require(std::abs(start.progress() - 1.0) < 1e-12, "final progress was not published");
}

static void rounded_threshold() {
    SmokeConcurrentStart start;
    std::atomic<bool> stop{false};
    start.checkpoint(10, 101, stop);
    require(!start.graph_ready(), "readiness threshold was rounded down below ten percent");
    auto writer = std::async(std::launch::async, [&] { start.checkpoint(11, 101, stop); });
    SmokeConcurrentStart::ReleaseGuard release_on_error(start);
    require(eventually([&] { return start.graph_ready(); }), "rounded readiness threshold was never reached");
    require(writer.wait_for(0s) == std::future_status::timeout, "writer did not park at the rounded threshold");
    start.release_readers();
    require(writer.wait_for(3s) == std::future_status::ready, "reader release did not unblock rounded threshold");
    writer.get();
}

static void stop_waiting() {
    SmokeConcurrentStart start;
    std::atomic<bool> stop{false};
    auto writer = std::async(std::launch::async, [&] { start.checkpoint(10, 100, stop); });
    SmokeConcurrentStart::ReleaseGuard release_on_error(start);
    require(eventually([&] { return start.graph_ready(); }), "writer never entered the handshake");
    stop.store(true);
    require(writer.wait_for(3s) == std::future_status::ready, "stop did not unblock a writer waiting for readers");
    writer.get();
}

static void exception_guard() {
    struct StartupFailure {};
    SmokeConcurrentStart start;
    std::atomic<bool> stop{false};
    auto writer = std::async(std::launch::async, [&] { start.checkpoint(10, 100, stop); });
    // The scoped guard must unwind before the async future is destroyed; the
    // future destructor waits for the writer and would otherwise deadlock.
    try {
        SmokeConcurrentStart::ReleaseGuard release_on_error(start);
        require(eventually([&] { return start.graph_ready(); }), "writer never entered the handshake");
        throw StartupFailure{};
    } catch (const StartupFailure&) {
    }
    const bool released = writer.wait_for(3s) == std::future_status::ready;
    // Keep a failed assertion from hanging on the future destructor.
    if (!released) start.release_readers();
    require(released, "exception guard did not release the waiting writer");
    writer.get();
}

static void monotonic_progress() {
    SmokeConcurrentStart start;
    std::atomic<bool> stop{false};
    std::atomic<bool> begin{false};
    std::atomic<unsigned> finished{0};
    std::vector<std::thread> workers;
    for (unsigned worker = 0; worker < 8; ++worker) {
        workers.emplace_back([&, worker] {
            while (!begin.load()) std::this_thread::yield();
            for (unsigned sample = 0; sample < 1000; ++sample) {
                // Stay below the readiness threshold while racing old and new
                // progress reports from independent workers.
                start.checkpoint((sample * 17 + worker * 11) % 99 + 1, 1000, stop);
            }
            ++finished;
        });
    }
    begin.store(true);
    bool monotonic = true;
    double previous = 0;
    while (finished.load() != workers.size()) {
        const double current = start.progress();
        if (current < previous) monotonic = false;
        previous = current;
        std::this_thread::yield();
    }
    for (auto& worker : workers) worker.join();
    start.checkpoint(1, 1000, stop);
    require(monotonic, "concurrent progress moved backwards");
    require(std::abs(start.progress() - 0.099) < 1e-12, "stale worker checkpoint overwrote maximum progress");
    require(!start.graph_ready(), "subthreshold worker progress announced readiness");
}

int main(int argc, char** argv) {
    try {
        require(argc == 2, "one test case must be selected");
        const std::string selected = argv[1];
        if (selected == "disabled") disabled();
        else if (selected == "delayed_reader") delayed_reader();
        else if (selected == "rounded_threshold") rounded_threshold();
        else if (selected == "stop_waiting") stop_waiting();
        else if (selected == "exception_guard") exception_guard();
        else if (selected == "monotonic_progress") monotonic_progress();
        else throw std::runtime_error("unknown test case");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
"""


@unittest.skipUnless(shutil.which("g++"), "g++ is needed to validate the C++ reader-start handshake")
class SmokeConcurrentStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.source = Path(cls.temp.name) / "concurrent_start.cpp"
        cls.source.write_text(HARNESS)
        cls.binary = Path(cls.temp.name) / "concurrent_start"
        compiled = subprocess.run(
            ["g++", "-std=c++17", "-O2", "-pthread", "-I", str(BUILD),
             str(cls.source), "-o", str(cls.binary)],
            capture_output=True, text=True, timeout=30)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)

    def run_case(self, case, enabled="1"):
        environment = dict(os.environ)
        environment.pop("RG_SMOKE_CONCURRENT_START", None)
        if enabled is not None:
            environment["RG_SMOKE_CONCURRENT_START"] = enabled
        checked = subprocess.run(
            [str(self.binary), case], env=environment,
            capture_output=True, text=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_disabled_by_default_returns_without_readers(self):
        self.run_case("disabled", enabled=None)

    def test_explicit_zero_returns_without_readers(self):
        self.run_case("disabled", enabled="0")

    def test_delayed_reader_preserves_pending_writes(self):
        self.run_case("delayed_reader")

    def test_threshold_rounds_up(self):
        self.run_case("rounded_threshold")

    def test_stop_unblocks_waiting_writer(self):
        self.run_case("stop_waiting")

    def test_exception_guard_releases_before_future_destruction(self):
        self.run_case("exception_guard")

    def test_progress_is_monotonic_across_workers(self):
        self.run_case("monotonic_progress")


if __name__ == "__main__":
    unittest.main()
