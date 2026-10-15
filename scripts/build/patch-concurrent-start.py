#!/usr/bin/env python3
"""Coordinate smoke readers with aging workers without changing paper startup."""
from pathlib import Path
import sys


STARTUP = """      chrono::seconds progress_check_interval( 1 );
      this_thread::sleep_for( progress_check_interval );  // Poor mans synchronization to ensure AgingExperiment was able to setup the master etc
      while (true) {
        if (m_aging_experiment.progress_so_far() > 0.1 || aging_result_future.wait_for(std::chrono::seconds(0)) == std::future_status::ready) { // The graph reached its final size
          break;
        }
        this_thread::sleep_for( progress_check_interval ) ;
      }"""
READ_LOOP = """      while (m_aging_experiment.progress_so_far() < 0.9 && aging_result_future.wait_for(std::chrono::seconds(0)) != std::future_status::ready) {
        m_graphalytics.execute();
        num_tasks_executed++;
      }"""

PATCHES = (
    ("main_driver.cpp", (
        # Make the stack-allocated experiment's caller rebuild as well: the
        # upstream Makefile does not track this header dependency reliably.
        ('#include "experiment/aging2_experiment.hpp"',
         '#include "experiment/aging2_experiment.hpp"\n#include "experiment/details/smoke_concurrent_start.hpp"'),
    )),
    ("experiment/aging2_experiment.hpp", (
        ('#include "details/aging2_master.hpp"',
         '#include "details/aging2_master.hpp"\n#include "details/smoke_concurrent_start.hpp"'),
        ("    details::Aging2Master* m_master;",
         "    details::Aging2Master* m_master;\n    details::SmokeConcurrentStart m_smoke_start;"),
        ("    double progress_so_far();",
         "    double progress_so_far();\n    const details::SmokeConcurrentStart& smoke_start() const { return m_smoke_start; }"),
    )),
    ("experiment/details/aging2_worker.cpp", (
        ("    const double reports_per_ops = m_master.parameters().m_num_reports_per_operations;",
         "    const double reports_per_ops = m_master.parameters().m_num_reports_per_operations;\n"
         "    const bool smoke_start = m_master.parameters().smoke_start().enabled();"),
        ("            uint64_t num_ops_done = m_master.m_num_operations_performed.fetch_add( end - start );",
         "            uint64_t num_ops_done = m_master.m_num_operations_performed.fetch_add( end - start );\n"
         "            if (smoke_start) {\n"
         "                m_master.parameters().smoke_start().checkpoint(\n"
         "                    num_ops_done + end - start, num_total_ops, m_master.m_stop_experiment);\n"
         "            }"),
    )),
    ("experiment/mixed_workload.cpp", (
        ("#include <chrono>", "#include <chrono>\n#include <stdexcept>"),
        ("      auto aging_result_future = std::async(std::launch::async, &Aging2Experiment::execute, &m_aging_experiment);",
         "      const auto& smoke_start = m_aging_experiment.smoke_start();\n"
         "      auto aging_result_future = std::async(std::launch::async, &Aging2Experiment::execute, &m_aging_experiment);\n"
         "      // Release parked workers before the future waits during exception unwinding.\n"
         "      details::SmokeConcurrentStart::ReleaseGuard smoke_release(smoke_start);"),
        (STARTUP,
         "      if (smoke_start.enabled()) {\n"
         "        // Read atomically published progress, never the worker-owned master pointer.\n"
         "        while (!smoke_start.graph_ready() && aging_result_future.wait_for(0s) != std::future_status::ready) {\n"
         "          this_thread::sleep_for(chrono::milliseconds(1));\n"
         "        }\n"
         "        if (!smoke_start.graph_ready()) {\n"
         "          aging_result_future.get(); // Propagate an update failure before warmup.\n"
         "          throw runtime_error(\"Smoke concurrent workload ended before reader startup; use a larger graphlog\");\n"
         "        }\n"
         "        cout << \"[Pipeline] Smoke concurrent warmup reached; starting readers and releasing writers\" << endl;\n"
         "      } else {\n" + STARTUP + "\n      }"),
        (READ_LOOP,
         "      if (smoke_start.enabled()) {\n"
         "        smoke_start.release_readers();\n"
         "        // The warmup gate left pending updates. Always perform the first real read\n"
         "        // round, even if the writer crosses 90% before the reader is rescheduled.\n"
         "        do {\n"
         "          m_graphalytics.execute();\n"
         "          num_tasks_executed++;\n"
         "        } while (smoke_start.progress() < 0.9 && aging_result_future.wait_for(0s) != std::future_status::ready);\n"
         "      } else {\n" + READ_LOOP + "\n      }"),
    )),
)


def patch_concurrent_start(root):
    root = Path(root)
    updates = []
    for relative, replacements in PATCHES:
        path = root / relative
        original = path.read_text()
        text = original
        for old, new in replacements:
            # Some replacements retain their old text as a prefix or an else branch.
            if text.count(new) == 1 and text.count(old) == new.count(old):
                continue
            if text.count(old) != 1 or text.count(new) != 0:
                raise SystemExit(f"GFE concurrent startup changed in {path}; inspect reader/writer coordination before building")
            text = text.replace(old, new)
        updates.append((path, original, text))
    header = root / "experiment/details/smoke_concurrent_start.hpp"
    expected = Path(__file__).with_name(header.name).read_text()
    if header.exists() and header.read_text() != expected:
        raise SystemExit(f"GFE smoke startup helper changed in {header}; inspect before building")
    updates.append((header, header.read_text() if header.exists() else None, expected))
    # Check every pinned source before writing any updates.
    for path, original, text in updates:
        if text != original:
            path.write_text(text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch-concurrent-start.py GFE_DIR")
    patch_concurrent_start(Path(sys.argv[1]))
