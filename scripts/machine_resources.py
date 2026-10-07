"""Read CPU and RAM capacity available to this process, including cgroup limits."""
import os
from pathlib import Path, PurePosixPath
import re


PROC_ROOT = Path("/proc")


def _read(path):
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return ""


def _sysconf(name):
    try:
        value = os.sysconf(name)
        return value if value > 0 else None
    except (OSError, ValueError, AttributeError):
        return None


def _host_memory():
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB\s*$", _read(PROC_ROOT / "meminfo"), re.M)
    if match:
        return int(match.group(1)) * 1024
    pages, page_size = _sysconf("SC_PHYS_PAGES"), _sysconf("SC_PAGE_SIZE")
    return pages * page_size if pages is not None and page_size is not None else None


def _unescape_mount(value):
    # mountinfo escapes spaces, tabs, newlines and backslashes as octal bytes.
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _cgroup_directories():
    """Yield (version, controllers, directory) for every visible ancestor."""
    memberships = []
    for line in _read(PROC_ROOT / "self/cgroup").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3:
            memberships.append((set(filter(None, fields[1].split(","))), PurePosixPath(fields[2])))

    seen = set()
    for line in _read(PROC_ROOT / "self/mountinfo").splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            kind = fields[separator + 1]
            if kind not in {"cgroup", "cgroup2"}:
                continue
            root = PurePosixPath(_unescape_mount(fields[3]))
            mount = Path(_unescape_mount(fields[4]))
            controllers = set(fields[separator + 3].split(","))
        except (ValueError, IndexError):
            continue
        for members, group in memberships:
            if kind == "cgroup2":
                if members:
                    continue
            elif not members.intersection(controllers):
                continue
            if not group.is_absolute() or ".." in group.parts:
                continue
            try:
                relative = group.relative_to(root)
            except ValueError:
                # In a cgroup namespace, /proc paths are relative to the
                # namespace root even when mountinfo records its host path.
                relative = group.relative_to("/")
            directory = mount.joinpath(*relative.parts)
            try:
                if not directory.is_dir():
                    continue
            except OSError:
                continue
            while True:
                key = (kind, tuple(sorted(controllers)), directory)
                if key not in seen:
                    seen.add(key)
                    yield kind, controllers, directory
                if directory == mount:
                    break
                directory = directory.parent


def _integer_limit(value, legacy=False):
    try:
        limit = int(value)
    except ValueError:
        return None
    # Legacy unlimited memory limits are huge page-aligned LONG_MAX values.
    if limit < 0 or (legacy and limit >= 1 << 60):
        return None
    return limit


def _cgroup_limits():
    cpu, memory = [], []
    for kind, controllers, directory in _cgroup_directories():
        if kind == "cgroup2":
            maximum = _read(directory / "cpu.max").split()
            quota, period = maximum if len(maximum) == 2 else ("", "")
            limit = _integer_limit(_read(directory / "memory.max"))
        else:
            quota = _read(directory / "cpu.cfs_quota_us") if "cpu" in controllers else ""
            period = _read(directory / "cpu.cfs_period_us") if "cpu" in controllers else ""
            limit = (_integer_limit(_read(directory / "memory.limit_in_bytes"), legacy=True)
                     if "memory" in controllers else None)
        try:
            quota, period = int(quota), int(period)
            if quota > 0 and period > 0:
                cpu.append(quota / period)
        except ValueError:
            pass
        if limit is not None:
            memory.append(limit)
    return min(cpu, default=None), min(memory, default=None)


def detect_resources():
    """Return host capacity and the effective capacity permitted to this process."""
    host_cpu = os.cpu_count() or _sysconf("SC_NPROCESSORS_ONLN")
    try:
        affinity_cpu = len(os.sched_getaffinity(0)) or None
    except (AttributeError, OSError):
        affinity_cpu = None
    host_memory = _host_memory()
    quota, memory_limit = _cgroup_limits()
    cpu_counts = [value for value in (host_cpu, affinity_cpu, quota) if value is not None]
    memories = [value for value in (host_memory, memory_limit) if value is not None]
    return dict(cpu_threads=min(cpu_counts, default=None), host_cpu_threads=host_cpu,
                affinity_cpu_threads=affinity_cpu, cpu_quota_threads=quota,
                memory_bytes=min(memories, default=None), host_memory_bytes=host_memory,
                memory_limit_bytes=memory_limit)
