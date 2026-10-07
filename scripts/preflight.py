"""Check the documented hardware minimums before starting paper workloads."""
import os
from pathlib import Path
import shutil
import stat

from machine_resources import detect_resources

GB = 1_000_000_000
MIN_CPU_THREADS = 64
MIN_MEMORY_BYTES = 256 * GB
MIN_STORAGE_BYTES = 512 * GB


class RequirementsError(RuntimeError):
    pass


def existing_directory(path):
    """Find the filesystem for a destination without creating directories."""
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    if not path.is_dir():
        raise RequirementsError(f"{path} exists but is not a directory")
    return path


def allocated_cache_bytes(path):
    """Count allocated blocks on the cache filesystem, once per inode."""
    path = Path(path)
    if not path.exists():
        return 0
    device = path.stat().st_dev
    seen = set()
    pending = [path]
    total = 0
    while pending:
        entry = pending.pop()
        info = entry.lstat()
        identity = (info.st_dev, info.st_ino)
        if info.st_dev != device or identity in seen or stat.S_ISLNK(info.st_mode):
            continue
        seen.add(identity)
        total += getattr(info, "st_blocks", (info.st_size + 511) // 512) * 512
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(entry) as children:
                pending.extend(Path(child.path) for child in children)
    return total


def check_requirements(args):
    """Return measured resources; reject shortfalls unless --force is set."""
    if args.profile != "paper" or set(args.stages) == {"report"}:
        return {"status": "skipped", "reason": "full-paper minimums do not apply to smoke or report-only runs"}

    measured = detect_resources()
    errors = []
    cpu = measured["cpu_threads"]
    if cpu is None:
        errors.append("CPU: cannot determine the number of usable logical CPU threads")
    elif cpu < MIN_CPU_THREADS:
        errors.append(f"CPU: {cpu:g} usable logical threads; at least {MIN_CPU_THREADS} required "
                      "(including CPU affinity and container CPU quotas)")
    memory = measured["memory_bytes"]
    if memory is None:
        errors.append("RAM: cannot determine the accessible physical memory")
    elif memory < MIN_MEMORY_BYTES:
        errors.append(f"RAM: {memory / GB:.1f} GB accessible; at least {MIN_MEMORY_BYTES / GB:g} GB required "
                      "(including container memory limits; swap is excluded)")

    data = args.data_dir.resolve()
    output = (args.run_dir or args.output_dir).resolve()
    for label, destination in (("dataset cache", data), ("output", output)):
        try:
            parent = existing_directory(destination)
            if not os.access(parent, os.W_OK | os.X_OK):
                errors.append(f"{label}: {parent} is not writable by UID {os.geteuid()}; "
                              "check the bind mount ownership and permissions")
            disk = shutil.disk_usage(parent)
            if label == "dataset cache":
                cached = allocated_cache_bytes(data)
                measured["storage"] = dict(path=str(data), free_bytes=disk.free,
                                           cached_bytes=cached, budget_bytes=disk.free + cached)
                if disk.free + cached < MIN_STORAGE_BYTES:
                    errors.append(f"Disk at {data}: {disk.free / GB:.1f} GB free + {cached / GB:.1f} GB "
                                  f"existing cache; at least {MIN_STORAGE_BYTES / GB:g} GB combined required")
            elif disk.free == 0:
                errors.append(f"Output disk at {output}: no free space for logs, figures or tables")
        except (OSError, RequirementsError) as error:
            errors.append(f"{label} at {destination}: {error}")

    if errors:
        if args.force:
            measured.update(status="forced", unmet_requirements=errors)
            return measured
        raise RequirementsError("Minimum requirements not satisfied:\n" +
                                "\n".join("  - " + error for error in errors) +
                                "\nNo datasets were downloaded and no experiments were started.")
    measured["status"] = "ok"
    return measured


def print_requirements(measured):
    if measured["status"] == "forced":
        print("[requirements] WARNING: --force overrides unmet requirements:\n" +
              "\n".join("  - " + error for error in measured["unmet_requirements"]), flush=True)
        return
    if measured["status"] == "skipped":
        print("[requirements] " + measured["reason"], flush=True)
        return
    storage = measured["storage"]
    print(f"[requirements] OK: {measured['cpu_threads']:g} usable CPU threads, "
          f"{measured['memory_bytes'] / GB:.1f} GB RAM, "
          f"{storage['free_bytes'] / GB:.1f} GB free + "
          f"{storage['cached_bytes'] / GB:.1f} GB dataset cache", flush=True)
