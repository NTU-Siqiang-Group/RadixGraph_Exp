from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import machine_resources


GIB = 1024 ** 3


class MachineResourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "meminfo").write_text(f"MemTotal: {512 * GIB // 1024} kB\n")
        (self.proc / "self/cgroup").write_text("")
        (self.proc / "self/mountinfo").write_text("")
        self.addCleanup(patch.stopall)
        patch.object(machine_resources, "PROC_ROOT", self.proc).start()
        patch.object(machine_resources.os, "cpu_count", return_value=128).start()
        patch.object(machine_resources.os, "sched_getaffinity", return_value=set(range(64))).start()
        # Prevent a missing fixture field from accidentally using host sysconf.
        patch.object(machine_resources.os, "sysconf", side_effect=ValueError).start()

    def mount(self, name, kind="cgroup2", root="/", controllers="rw"):
        directory = self.root / name
        directory.mkdir(parents=True)
        encoded = str(directory).replace("\\", "\\134").replace(" ", "\\040")
        with (self.proc / "self/mountinfo").open("a") as stream:
            stream.write(f"42 23 0:40 {root} {encoded} rw - {kind} cgroup {controllers}\n")
        return directory

    def test_host_and_affinity_without_cgroups(self):
        values = machine_resources.detect_resources()
        self.assertEqual(values, dict(cpu_threads=64, host_cpu_threads=128,
                                     affinity_cpu_threads=64, cpu_quota_threads=None,
                                     memory_bytes=512 * GIB, host_memory_bytes=512 * GIB,
                                     memory_limit_bytes=None))

    def test_v2_ancestor_limits_are_stricter_than_leaf(self):
        mount = self.mount("unified")
        group = mount / "team/job"
        group.mkdir(parents=True)
        (self.proc / "self/cgroup").write_text("0::/team/job\n")
        (mount / "cpu.max").write_text("max 100000")
        (mount / "memory.max").write_text("max")
        (mount / "team/cpu.max").write_text("250000 100000")
        (mount / "team/memory.max").write_text(str(128 * GIB))
        (group / "cpu.max").write_text("800000 100000")
        (group / "memory.max").write_text(str(256 * GIB))
        values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 2.5)
        self.assertEqual(values["cpu_quota_threads"], 2.5)
        self.assertEqual(values["memory_bytes"], 128 * GIB)

    def test_v1_separate_controllers_and_unlimited_memory(self):
        cpu = self.mount("cpu", kind="cgroup", controllers="rw,cpu,cpuacct")
        memory = self.mount("memory", kind="cgroup", controllers="rw,memory")
        (self.proc / "self/cgroup").write_text("2:cpu,cpuacct:/team/job\n3:memory:/team/job\n")
        for mount in (cpu, memory):
            (mount / "team/job").mkdir(parents=True)
        (cpu / "cpu.cfs_quota_us").write_text("-1")
        (cpu / "cpu.cfs_period_us").write_text("100000")
        (cpu / "team/cpu.cfs_quota_us").write_text("400000")
        (cpu / "team/cpu.cfs_period_us").write_text("100000")
        (memory / "memory.limit_in_bytes").write_text(str((1 << 63) - 4096))
        (memory / "team/job/memory.limit_in_bytes").write_text(str(96 * GIB))
        values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 4)
        self.assertEqual(values["memory_limit_bytes"], 96 * GIB)
        self.assertEqual(values["memory_bytes"], 96 * GIB)

    def test_namespaced_root_and_escaped_mount_point(self):
        mount = self.mount("namespace mount", root="/docker/container")
        (self.proc / "self/cgroup").write_text("0::/\n")
        (mount / "cpu.max").write_text("1600000 100000")
        (mount / "memory.max").write_text(str(128 * GIB))
        values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 16)
        self.assertEqual(values["memory_bytes"], 128 * GIB)

    def test_mount_root_is_removed_from_host_cgroup_path(self):
        mount = self.mount("subtree", root="/docker/container")
        (mount / "worker").mkdir()
        (self.proc / "self/cgroup").write_text("0::/docker/container/worker\n")
        (mount / "worker/cpu.max").write_text("800000 100000")
        (mount / "memory.max").write_text(str(192 * GIB))
        values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 8)
        self.assertEqual(values["memory_bytes"], 192 * GIB)

    def test_unlimited_and_invalid_limits_preserve_host_capacity(self):
        mount = self.mount("unlimited")
        (mount / "child").mkdir()
        (self.proc / "self/cgroup").write_text("0::/child\n")
        (mount / "cpu.max").write_text("max 100000")
        (mount / "memory.max").write_text("max")
        (mount / "child/cpu.max").write_text("broken")
        (mount / "child/memory.max").write_text("invalid")
        values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 64)
        self.assertEqual(values["memory_bytes"], 512 * GIB)
        self.assertIsNone(values["cpu_quota_threads"])
        self.assertIsNone(values["memory_limit_bytes"])

    def test_affinity_and_physical_memory_can_be_stricter_than_limits(self):
        mount = self.mount("larger-limit")
        (self.proc / "self/cgroup").write_text("0::/\n")
        (mount / "cpu.max").write_text("8000000 100000")
        (mount / "memory.max").write_text(str(1024 * GIB))
        values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 64)
        self.assertEqual(values["cpu_quota_threads"], 80)
        self.assertEqual(values["memory_bytes"], 512 * GIB)
        self.assertEqual(values["memory_limit_bytes"], 1024 * GIB)

    def test_unreadable_leaf_limits_do_not_hide_readable_ancestor_limits(self):
        mount = self.mount("partially-readable")
        leaf = mount / "child"
        leaf.mkdir()
        (self.proc / "self/cgroup").write_text("0::/child\n")
        (mount / "cpu.max").write_text("400000 100000")
        (mount / "memory.max").write_text(str(128 * GIB))
        original_read = Path.read_text

        def read(path, *args, **kwargs):
            if path.parent == leaf:
                raise PermissionError("fixture denies leaf limit reads")
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 4)
        self.assertEqual(values["memory_bytes"], 128 * GIB)

    def test_sysconf_fallback_when_proc_memory_and_affinity_are_unavailable(self):
        (self.proc / "meminfo").unlink()
        patch.object(machine_resources.os, "sched_getaffinity", side_effect=OSError).start()
        patch.object(machine_resources.os, "cpu_count", return_value=None).start()
        patch.object(machine_resources.os, "sysconf", side_effect=lambda key: {
            "SC_NPROCESSORS_ONLN": 32, "SC_PHYS_PAGES": 64 * GIB // 4096,
            "SC_PAGE_SIZE": 4096}[key]).start()
        values = machine_resources.detect_resources()
        self.assertEqual(values["cpu_threads"], 32)
        self.assertIsNone(values["affinity_cpu_threads"])
        self.assertEqual(values["memory_bytes"], 64 * GIB)

    def test_unknown_resources_are_reported_as_unknown(self):
        (self.proc / "meminfo").unlink()
        patch.object(machine_resources.os, "sched_getaffinity", side_effect=OSError).start()
        patch.object(machine_resources.os, "cpu_count", return_value=None).start()
        values = machine_resources.detect_resources()
        self.assertIsNone(values["cpu_threads"])
        self.assertIsNone(values["memory_bytes"])


if __name__ == "__main__":
    unittest.main()
