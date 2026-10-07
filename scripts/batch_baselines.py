"""Table 7 native Terrace, Aspen, and CPAM batch benchmark integration.

The notebook leaves baseline setup to their upstream projects. These wrappers
use their public batch APIs with the paper's dataset/batch/trial selections.
They deliberately measure process RSS instead of upstream theoretical sizes.
"""
import csv
import json
import math
import os
from pathlib import Path

BATCH_SIZES = (10, 100, 1000, 10000)
PAPER_DATASETS = {"com-lj.ungraph", "com-orkut.ungraph", "twitter-2010"}
BASELINES = {"terrace": ("Terrace", "OpenMP"),
             "aspen": ("Aspen", "upstream homegrown scheduler"),
             "cpam": ("CPAM", "ParlayLib scheduler, difference encoding")}
FIELDS = ("dataset", "method", "batch_size", "insert_ops_s", "delete_ops_s",
          "memory_gib", "status", "source_log")
PROTOCOL = {
    "initial_graph": "symmetrized, deduplicated, unweighted input; original 32-bit vertex IDs",
    "updates": "uniform endpoints among vertices present in the input; replacement allowed",
    "rng": "C++ mt19937_64 seed 42; differs from upstream RadixGraph C rand sequence",
    "aggregation": "nominal batch size divided by mean wall time, 20 trials in paper profile",
    "timing": "native API call, including required sorting/materialization; endpoint generation excluded",
    "memory": "post graph-initialization VmRSS minus pre-initialization VmRSS, input CSR retained",
    "limits": "Baseline graph constructors are included in RSS delta; RadixGraph's upstream metric samples after its constructor. Native schedulers differ from historical Cilk artifacts. The paper does not publish exact baseline revisions/driver settings. CPAM diff encoder object construction is patched to avoid assigning nested maps into uninitialized storage.",
}


def parse_measurements(text):
    """Read machine-readable markers while ignoring verbose upstream logging."""
    memory = None
    batches = {}
    threads = None
    graph = None
    for line in text.splitlines():
        if line.startswith("BATCH_MEMORY "):
            memory = json.loads(line[len("BATCH_MEMORY "):])
        elif line.startswith("BATCH_RESULT "):
            value = json.loads(line[len("BATCH_RESULT "):])
            batch = int(value["batch_size"])
            if batch in batches:
                raise ValueError(f"Duplicate batch measurement: {batch}")
            for metric in ("insert_ops_s", "delete_ops_s"):
                if not math.isfinite(value[metric]) or value[metric] <= 0:
                    raise ValueError(f"Invalid {metric} for batch {batch}")
            batches[batch] = value
        elif line.startswith("BATCH_THREADS "):
            threads = int(line[len("BATCH_THREADS "):])
        elif line.startswith("BATCH_GRAPH "):
            graph = json.loads(line[len("BATCH_GRAPH "):])
    if (memory is None or memory.get("metric") != "graph_load_rss_delta"
            or not math.isfinite(memory["memory_gib"]) or memory["memory_gib"] < 0):
        raise ValueError("Missing or invalid measured graph-load RSS delta")
    delta = (memory["rss_after_bytes"] - memory["rss_before_bytes"]) / (1024 ** 3)
    if not math.isclose(delta, memory["memory_gib"], rel_tol=1e-8, abs_tol=1e-12):
        raise ValueError("RSS byte samples do not match reported GiB")
    if set(batches) != set(BATCH_SIZES):
        raise ValueError("Missing paper batch sizes: " + str(sorted(set(BATCH_SIZES) - set(batches))))
    if threads is None or threads < 1:
        raise ValueError("Missing actual worker count")
    return dict(memory=memory, batches=batches, threads=threads, graph=graph)


def _save_records(output, records):
    directory = output / "batch"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "records.json.tmp"
    temporary.write_text(json.dumps(records, indent=2) + "\n")
    temporary.replace(directory / "records.json")


def run_batch_baselines(p):
    root = Path(os.environ.get("RADIXGRAPH_BATCH_ROOT", "/workspace/batch"))
    records = []
    for spec in p.dataset_specs:
        if p.profile == "paper" and spec["name"] not in PAPER_DATASETS:
            continue
        for slug, (method, backend) in BASELINES.items():
            name = f"batch-{slug}-{spec['name']}"
            trials = 20 if p.profile == "paper" else 2
            metadata = dict(artifact="table7", suite="batch_baselines", kind="batch",
                            dataset=spec["name"], method=method, backend=backend,
                            threads=p.threads, trials=trials, batch_sizes=list(BATCH_SIZES), protocol=PROTOCOL)
            binary = root / slug / "batch_benchmark"
            edge = Path(spec["edge"])
            if not binary.is_file():
                result = p.note(name, "missing", f"Baseline executable missing: {binary}; rebuild Docker image", metadata)
            elif not edge.is_file():
                result = p.note(name, "missing", f"Dataset edge file missing: {edge}", metadata)
            else:
                command = ["env", f"OMP_NUM_THREADS={p.threads}", "OMP_DYNAMIC=FALSE",
                           f"NUM_THREADS={p.threads}", f"PARLAY_NUM_THREADS={p.threads}",
                           binary, edge, str(trials)]
                try:
                    result = p.run(name, command, root / slug, metadata=metadata)
                except KeyboardInterrupt:
                    # Pipeline.run persists its interrupted job before raising.
                    jobs = getattr(p, "jobs", [])
                    current = next((job for job in reversed(jobs) if job.get("name") == name),
                                   dict(name=name, status="interrupted", log="", metadata=metadata))
                    records.append(dict(current))
                    _save_records(p.output, records)
                    render_batch_reports(p.output)
                    raise
            record = dict(result)
            record["metadata"] = metadata
            if result["status"] == "ok":
                try:
                    measured = parse_measurements(Path(result["log"]).read_text())
                    if measured["threads"] != p.threads:
                        raise ValueError(f"Requested {p.threads} workers, measured {measured['threads']}")
                    if any(value.get("trials") != trials for value in measured["batches"].values()):
                        raise ValueError("Measured trial count differs from selected profile")
                    record["measurements"] = measured
                except (OSError, ValueError, KeyError, TypeError) as error:
                    record.update(status="failed", reason=f"Invalid batch output: {error}")
                    p.note(name + "-validation", "failed", record["reason"], metadata)
            records.append(record)
            _save_records(p.output, records)
    versions = root / "source-versions.json"
    if versions.is_file():
        (p.output / "batch").mkdir(exist_ok=True)
        (p.output / "batch" / "source-versions.json").write_text(versions.read_text())
    render_batch_reports(p.output)


def _resolve_log(output, value):
    path = Path(value)
    if not path.is_absolute():
        return output / path
    if "logs" in path.parts:
        # Once exported, the run's retained copy is the evidence even when an
        # original container path happens to remain accessible on this host.
        return output.joinpath(*path.parts[path.parts.index("logs"):])
    return path


def render_batch_reports(output):
    output = Path(output)
    path = output / "batch" / "records.json"
    # The pipeline manifest is authoritative, including a job interrupted before
    # this module could update its secondary record file.
    if (output / "manifest.json").is_file():
        records = [job for job in json.loads((output / "manifest.json").read_text()).get("jobs", [])
                   if job.get("metadata", {}).get("suite") == "batch_baselines"
                   and not job.get("name", "").endswith("-validation")]
    elif path.is_file():
        records = json.loads(path.read_text())
    else:
        return dict(issues=[], rows=0)
    rows = []
    raw = []
    issues = []
    for record in records:
        metadata = record.get("metadata", {})
        measured = None
        status = record.get("status", "missing")
        logfile = _resolve_log(output, record.get("log", ""))
        try:
            source = str(logfile.relative_to(output)) if record.get("log") else ""
        except ValueError:
            source = str(logfile)
        if status == "ok":
            try:
                # Reparse logs so report-only mode detects lost/truncated evidence.
                measured = parse_measurements(logfile.read_text())
                if measured["threads"] != metadata["threads"]:
                    raise ValueError("Worker count mismatch")
                if metadata.get("trials") and any(value.get("trials") != metadata["trials"]
                                                   for value in measured["batches"].values()):
                    raise ValueError("Trial count mismatch")
            except (OSError, ValueError, KeyError, TypeError) as error:
                status = "failed"
                measured = None
                issues.append(dict(artifact="table7", status="parse_error", source_log=source,
                                   detail=str(error)))
        else:
            issues.append(dict(artifact="table7", status=status, source_log=source,
                               detail=record.get("reason", "Batch benchmark did not complete")))
        raw.append(dict(dataset=metadata.get("dataset"), method=metadata.get("method"),
                        status=status, measurements=measured, source_log=source))
        for batch in BATCH_SIZES:
            value = measured["batches"].get(batch, measured["batches"].get(str(batch), {})) if measured else {}
            rows.append(dict(dataset=metadata.get("dataset", ""), method=metadata.get("method", ""),
                             batch_size=batch, insert_ops_s=value.get("insert_ops_s", ""),
                             delete_ops_s=value.get("delete_ops_s", ""),
                             memory_gib=measured["memory"]["memory_gib"] if measured else "",
                             status=status, source_log=source))
    tables = output / "tables"
    tables.mkdir(exist_ok=True)
    with (tables / "table7_baselines.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    batch_dir = output / "batch"
    batch_dir.mkdir(exist_ok=True)
    (batch_dir / "measurements.json").write_text(json.dumps(dict(protocol=PROTOCOL, results=raw), indent=2) + "\n")
    (tables / "table7_baselines_notes.md").write_text(
        "# Table 7 baseline measurement protocol\n\n" +
        "\n".join(f"- **{name}:** {description}" for name, description in PROTOCOL.items()) +
        "\n\nMissing/failed rows are retained with empty measurements; no paper values are copied.\n")
    report = dict(issues=issues, rows=len(rows))
    (batch_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
