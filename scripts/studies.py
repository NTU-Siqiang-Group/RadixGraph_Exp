"""Run the standalone paper experiments without interactive stdin.

Every benchmark receives a private working directory. The record index and raw
stdout remain usable even when a later experiment fails or the run is stopped.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from .study_reports import parse_transform, render_study_reports
except ImportError:
    from study_reports import parse_transform, render_study_reports


STUDY_ARTIFACTS = frozenset({"figure12", "figure13", "table5", "table6", "table7"})
MEMORY_VARIANTS = ("Updated", "Trailing", "vEB")
WORKLOADS = ("u", "s", "h")
INDEX_BITS = (24, 32)
UPDATE_VARIANTS = ("sort", "art")
ANALYTICS_VARIANTS = ("sort", "sort-no-chain")
PAPER_BATCH_DATASETS = frozenset({"lj", "livejournal", "com-lj.ungraph", "orkut",
                                  "com-orkut.ungraph", "twitter", "twitter-2010"})


def _memory_counts(profile):
    return (1000, 10000) if profile == "smoke" else (100000, 1000000, 10000000)


def _index_counts(profile):
    return (1000, 10000) if profile == "smoke" else (10000, 100000, 1000000, 10000000)


def _batch_dataset_selected(profile, name):
    return profile == "smoke" or name.lower() in PAPER_BATCH_DATASETS


def planned_study_jobs(profile, dataset_names, artifacts=None):
    """Return the fixed benchmark names in execution order.

    Figure 12 optimizer calls are adaptive prerequisite work and are excluded.
    Missing prerequisites still produce a record for each planned benchmark.
    """
    artifacts = set(artifacts) if artifacts is not None else STUDY_ARTIFACTS
    names = []
    if "figure12" in artifacts:
        names.append("figure12-transformation")
        names.extend(f"figure12-memory-{variant.lower()}-{n}"
                     for n in _memory_counts(profile) for variant in MEMORY_VARIANTS)
    if "figure13" in artifacts:
        names.extend(f"figure13-workload-{workload}" for workload in WORKLOADS)
    if "table5" in artifacts:
        names.extend(f"table5-index-{n}-{bits}"
                     for n in _index_counts(profile) for bits in INDEX_BITS)
    for name in dataset_names:
        if "table6" in artifacts:
            names.extend(f"table6-updates-{variant}-{name}" for variant in UPDATE_VARIANTS)
            names.extend(f"table6-analytics-{variant}-{name}" for variant in ANALYTICS_VARIANTS)
        if "table7" in artifacts and _batch_dataset_selected(profile, name):
            names.append(f"table7-radixgraph-{name}")
    return names


def find_trailing_fanout(n, updated, configurations, optimize):
    """Find the immediately preceding distinct optimum below ``n``.

    Half the current cardinality can still belong to the same optimal interval.
    Use measured change points when they agree with the current optimum;
    otherwise bracket the previous interval and locate its upper boundary.
    ``optimize`` returns a fanout list or None when configuration is unavailable.
    """
    if updated is None or n <= 1:
        return None
    preceding = sorted((item for item in configurations if item["n"] <= n),
                       key=lambda item: item["n"])
    if preceding and preceding[-1]["fanout"] == updated:
        different = [item for item in preceding if item["fanout"] != updated]
        if different:
            return different[-1]["fanout"]
    upper, lower = n, max(1, n // 2)
    while True:
        candidate = optimize(lower)
        if candidate is None:
            return None
        if candidate != updated:
            break
        if lower == 1:
            return None
        upper, lower = lower, max(1, lower // 2)
    # Predicate: the optimum still differs from the current configuration.
    while upper - lower > 1:
        middle = (lower + upper) // 2
        result = optimize(middle)
        if result is None:
            return None
        if result == updated:
            upper = middle
        else:
            lower, candidate = middle, result
    return candidate


def run_studies(p, artifacts=None):
    """Run Figures 12–13, Tables 5–6, and RadixGraph's Table 7 rows.

    ``p`` supplies output/radix Paths, profile, threads, dataset_specs, and a
    run(name, argv, cwd, input_text=None, metadata=None) method.
    """
    artifacts = set(artifacts) if artifacts is not None else STUDY_ARTIFACTS
    root = Path(p.output) / "studies"
    root.mkdir(parents=True, exist_ok=True)
    index = root / "records.json"
    records = []

    def save():
        temporary = index.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2) + "\n")
        temporary.replace(index)

    def run(name, command, metadata, input_text=None):
        work = root / "work" / name
        work.mkdir(parents=True, exist_ok=True)
        try:
            result = p.run(name, [str(x) for x in command], cwd=work,
                           input_text=input_text, metadata=metadata)
        except KeyboardInterrupt:
            # Pipeline.run persists the interrupted job before re-raising.
            result = next((job for job in reversed(p.jobs) if job.get("name") == name),
                          {"name": name, "status": "interrupted", "log": ""})
            records.append({**result, "name": name, "metadata": metadata,
                            "workdir": str(work)})
            save()
            raise
        record = {**result, "name": name, "metadata": metadata,
                  "workdir": str(work)}
        records.append(record)
        save()
        return record

    def unavailable(name, metadata, reason):
        # Missing prerequisites are recorded explicitly, never silently skipped.
        work = root / "work" / name
        work.mkdir(parents=True, exist_ok=True)
        log = work / "unavailable.log"
        log.write_text(reason + "\n")
        record = {"name": name, "status": "missing", "exit_code": None,
                  "metadata": metadata, "log": str(log), "workdir": str(work)}
        records.append(record)
        save()
        p.note(name, "missing", reason, metadata)
        return record

    def log_text(record):
        path = Path(record.get("log", ""))
        return path.read_text(errors="replace") if path.is_file() else ""

    def binary(variant, name):
        return Path(p.radix) / "builds" / variant / name

    optimizer_cache = {}

    def optimizer(n, role):
        if n in optimizer_cache:
            return optimizer_cache[n]
        meta = {"artifact": "figure12", "kind": "optimizer", "n": n,
                "bits": 32, "role": role}
        record = run(f"figure12-optimizer-{role}-{n}",
                     [Path(p.radix) / "optimizer"], meta, f"{n}\n32\n5\n")
        settings = Path(record["workdir"]) / "settings.txt"
        if record["status"] != "ok" or not settings.is_file():
            return None
        try:
            values = [int(x) for x in settings.read_text().split()]
        except ValueError:
            return None
        if len(values) != 6 or values[0] != 5 or sum(values[1:]) != 32:
            return None
        optimizer_cache[n] = values[1:]
        return optimizer_cache[n]

    try:
        smoke = p.profile == "smoke"
        maximum, minimum = (10000, 1000) if smoke else (10000000, 100000)
        if "figure12" in artifacts:
            command = [binary("sort", "test_transform_continuous")]
            if smoke:
                command.extend([str(maximum), str(minimum)])
            transform = run("figure12-transformation", command,
                            {"artifact": "figure12", "kind": "transformation",
                             "n_min": minimum, "n_max": maximum, "bits": 32})
            configurations, _ = parse_transform(log_text(transform))
            for n in _memory_counts(p.profile):
                updated = optimizer(n, "updated")
                trailing = find_trailing_fanout(n, updated, configurations,
                                                lambda value: optimizer(value, "trailing"))
                for variant, fanout in zip(MEMORY_VARIANTS, (updated, trailing, [16, 8, 4, 2, 2])):
                    name = f"figure12-memory-{variant.lower()}-{n}"
                    meta = {"artifact": "figure12", "kind": "memory", "n": n,
                            "bits": 32, "variant": variant, "fanout": fanout}
                    if fanout is None:
                        unavailable(name, meta, "Optimizer did not produce valid fanout settings; see optimizer log.")
                        continue
                    stdin = f"{n}\n32\n5\n{' '.join(map(str, fanout))}\n"
                    run(name, [binary("sort", "test_trie")], meta, stdin)

        if "figure13" in artifacts:
            for workload in WORKLOADS:
                run(f"figure13-workload-{workload}",
                    [binary("sort", "test_trie_workload")],
                    {"artifact": "figure13", "kind": "workload", "n": maximum,
                     "bits": 32, "workload": workload}, f"{maximum}\n32\n{workload}\n")

        if "table5" in artifacts:
            for n in _index_counts(p.profile):
                for bits in INDEX_BITS:
                    run(f"table5-index-{n}-{bits}",
                        [binary("index", "test_vertex_index")],
                        {"artifact": "table5", "kind": "index", "n": n,
                         "bits": bits, "threads": p.threads}, f"{n}\n{bits}\n")

        for dataset in p.dataset_specs:
            name, edge = dataset["name"], Path(dataset["edge"])
            if "table6" in artifacts:
                for variant in UPDATE_VARIANTS:
                    meta = {"artifact": "table6", "kind": "updates",
                            "dataset": name, "variant": variant, "threads": p.threads}
                    if edge.is_file():
                        run(f"table6-updates-{variant}-{name}",
                            [binary(variant, "test_radixgraph"), edge], meta)
                    else:
                        unavailable(f"table6-updates-{variant}-{name}", meta,
                                    f"Dataset edge file is missing: {edge}")
                for variant in ANALYTICS_VARIANTS:
                    meta = {"artifact": "table6", "kind": "analytics",
                            "dataset": name, "variant": variant, "threads": p.threads}
                    if edge.is_file():
                        run(f"table6-analytics-{variant}-{name}",
                            [binary(variant, "test_analytics"), edge], meta)
                    else:
                        unavailable(f"table6-analytics-{variant}-{name}", meta,
                                    f"Dataset edge file is missing: {edge}")
            if "table7" in artifacts and _batch_dataset_selected(p.profile, name):
                meta = {"artifact": "table7", "kind": "batch", "dataset": name,
                        "method": "RadixGraph", "threads": p.threads}
                if edge.is_file():
                    run(f"table7-radixgraph-{name}",
                        [binary("sort", "test_batch_updates"), edge], meta)
                else:
                    unavailable(f"table7-radixgraph-{name}", meta,
                                f"Dataset edge file is missing: {edge}")
    finally:
        save()
        report = render_study_reports(Path(p.output), records, artifacts=artifacts)
        for issue in report["issues"]:
            if issue.get("status") == "parse_error":
                p.note(f"studies-parse-{issue.get('artifact')}", "failed",
                       f"{issue.get('detail')}: {issue.get('source_log')}",
                       {"artifact": issue.get("artifact"), "kind": "parse"})
    return records


if __name__ == "__main__":
    raise SystemExit("Use pipeline.py to run experiments; study_reports.py can regenerate artifacts.")
