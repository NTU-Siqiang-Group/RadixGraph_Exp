"""Export measured GFE tables for the unchanged upstream plotting scripts."""
import csv
import json
from pathlib import Path
import re
import sqlite3

NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
METHODS = {"teseo.13": "Teseo", "sortledton.4": "Sortledton", "bvgt": "Spruce", "gtx": "GTX", "radixgraph": "RadixGraph"}
DATASETS = ["com-lj.ungraph", "dota-league", "com-orkut.ungraph", "graph500-24", "uniform-24", "twitter-2010"]
LABELS = ["lj", "dota", "orkut", "g24", "u24", "twitter"]


def resolve_evidence(output, value):
    path = Path(value)
    if not path.is_absolute():
        return output / path
    for marker in ("logs", "raw"):
        if marker in path.parts:
            index = len(path.parts) - 1 - list(reversed(path.parts)).index(marker)
            return output.joinpath(*path.parts[index:])
    return path


def seconds(value):
    value = value.strip().rstrip(".,")
    days = re.match(r"(\d+)\s+day(?:\(s\)|s)?\s+and\s+(.+)", value)
    if days:
        return int(days[1]) * 86400 + seconds(days[2])
    match = re.fullmatch(r"(" + NUMBER + r")\s*(ns|us|µs|ms|s|secs?|seconds?|m|mins?|h|hours?|nanosecs?|microsecs?|millisecs?)?", value)
    if match:
        unit = match[2] or "s"
        scale = 1e-9 if unit.startswith("nano") or unit == "ns" else 1e-6 if unit.startswith("micro") or unit in ("us", "µs") else .001 if unit.startswith("milli") or unit == "ms" else 60 if unit in ("m", "min", "mins") else 3600 if unit in ("h", "hour", "hours") else 1
        return float(match[1]) * scale
    value = re.sub(r"\s+(?:mins?|hours?)$", "", value)
    parts = value.split(":")
    if 1 < len(parts) <= 3:
        result = 0
        for part in parts:
            result = result * 60 + float(part)
        return result
    raise ValueError("Unrecognized duration: " + value)


def elapsed(text, prefix):
    match = re.search(r"^" + re.escape(prefix) + r".*?\bin\s+(.+)$", text, re.M)
    if not match:
        return None
    try:
        return seconds(match[1])
    except ValueError:
        return None


def rate(count, duration, scale=1):
    return count / duration / scale if count is not None and duration and duration > 0 else None


def parse_log(text, experiment):
    values = {}
    loaded = re.search(r"^Loaded\s+(\d+)", text, re.M)
    count = int(loaded[1]) if loaded else None
    memory = re.search(r"^Memory consumption:\s*(" + NUMBER + r")\s*MB", text, re.M)
    if memory:
        values["memory_MB"] = float(memory[1])
    if experiment in ("random", "vertices"):
        values["insertion_Mops_s"] = rate(count, elapsed(text, "Insertions performed"), 1e6)
        if experiment == "random":
            values["deletion_Mops_s"] = rate(count, elapsed(text, "Deletions performed"), 1e6)
        else:
            values["query_Mops_s"] = rate(count, elapsed(text, "Vertex queries performed"), 1e6)
    if experiment == "analytics":
        for hop in (1, 2):
            match = re.search(r"^Get " + str(hop) + r"-hop neighbors for (\d+) vertices", text, re.M)
            values[f"hop{hop}_ops_s"] = rate(int(match[1]) if match else None, elapsed(text, f"Get {hop}-hop neighbors"))
        for source, label in (("BFS", "bfs"), ("SSSP", "sssp"), ("PageRank", "pr"), ("WCC", "wcc"), ("LCC", "tc"), ("BC", "bc")):
            # ExecStatistics stores/report means in microseconds, without a
            # unit suffix. Individual timer lines can be rounded to ms.
            summary = re.search(r"^>> " + source + r" N:\s*(\d+), mean:\s*(" + NUMBER + r").*?num timeouts:\s*(\d+)", text, re.M)
            if summary:
                trials, mean, timeouts = int(summary[1]), float(summary[2]), int(summary[3])
                values[label + "_s"] = mean / 1e6 if trials > timeouts else None
                values[label + "_timeouts"] = timeouts
            else:
                timings = []
                for entry in re.findall(r"^>> " + source + r" Execution time:\s*(.+)$", text, re.M):
                    try:
                        timings.append(seconds(entry))
                    except ValueError:
                        pass
                values[label + "_s"] = sum(timings) / len(timings) if timings else None
    return values


def database_series(database):
    """Use named DB columns; failed/truncated workloads retain their progress."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        aging = dict(connection.execute("SELECT * FROM aging ORDER BY rowid DESC LIMIT 1").fetchone())
        # Upstream creates sample tables lazily. A subsecond job can finish
        # before the first one-second sample, leaving this table absent.
        sampled = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='aging_intermediate_throughput3'").fetchone()
        rows = connection.execute("SELECT second, num_operations FROM aging_intermediate_throughput3 ORDER BY second").fetchall() if sampled else []
        total = aging["num_updates"]
        points = [(row["num_operations"] / total * 100, row["second"]) for row in rows if total and row["num_operations"]]
        termination = [key for key in ("has_terminated_for_timeout", "has_terminated_for_memfp", "has_terminated_deadlocked") if aging.get(key)]
        return points, aging, termination


def delete_series(text):
    # Match upstream's last worker deletion-start marker, retaining observed
    # progress rather than treating arbitrary sample indices as 10% steps.
    progress = 0
    start = None
    points = []
    performed = re.search(r"\[Pipeline\] Operations performed:\s*(\d+)\s*/\s*(\d+)", text)
    complete = performed and int(performed[1]) == int(performed[2]) and int(performed[2]) > 0
    for line in text.splitlines():
        match = re.search(r"Progress:\s*(\d+)%", line)
        if match:
            progress = int(match[1])
        if "Delete started." in line:
            start, points = progress, []
        match = re.match(r"Memory consumption:\s*(" + NUMBER + r")\s*MB", line)
        if match and start is not None and start < 100:
            points.append(((progress - start) * 100 / (100 - start), float(match[1])))
        final = re.match(r"\[Aging2\] Memory after:\s*(" + NUMBER + r")", line)
        if final and start is not None and start < 100:
            position = 100 if complete else (progress - start) * 100 / (100 - start)
            points.append((position, float(final[1])))
    return points


def concurrent_values(text, metadata, aging):
    values = {}
    match = re.search(r"Graphaltyics finished\. Number of rounds executed:\s*(\d+).*?in\s*(" + NUMBER + r")\s*s", text)
    if match:
        rounds, duration = int(match[1]), float(match[2])
        # Upstream graphalytics.cpp loops IDs 0..317727 (inclusive) for dota
        # in 1-hop mode and samples exactly 1000 start IDs in 2-hop mode.
        per_round = 317728 if metadata["hop"] == 1 else 1000
        values["read_ops_s"] = rate(rounds * per_round, duration) if rounds else None
        values["rounds"] = rounds
    if aging:
        completion = aging.get("completion_time", 0) / 1e6
        verified = re.search(r"\[Pipeline\] Operations performed:\s*(\d+)\s*/\s*(\d+)", text)
        performed = int(verified[1]) if verified else None
        if not any(aging.get(key) for key in ("has_terminated_for_timeout", "has_terminated_for_memfp", "has_terminated_deadlocked")):
            values["write_Mops_s"] = rate(performed, completion, 1e6)
    return values


def write_csv(path, rows):
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys or ["status"])
        writer.writeheader()
        writer.writerows(rows)


def render_gfe_reports(output, records, profile="paper"):
    output = Path(output)
    tables, figures = output / "tables", output / "figures"
    tables.mkdir(exist_ok=True)
    figures.mkdir(exist_ok=True)
    rows, mixed, deletion, concurrent = [], [], [], []
    issues = []
    for record in records:
        meta = record.get("metadata", {})
        if meta.get("suite") != "gfe":
            continue
        experiment = meta["experiment"]
        log = resolve_evidence(output, record["log"]) if record.get("log") else None
        text = log.read_text(errors="replace") if log and log.is_file() else ""
        values = parse_log(text, experiment)
        row = dict(system=meta["system"], dataset=meta["dataset"], experiment=experiment,
                   status=record["status"], log=record.get("log", ""), **values)
        if experiment in ("random", "vertices", "analytics"):
            rows.append(row)
            required = {"random": ("insertion_Mops_s", "deletion_Mops_s", "memory_MB"),
                        "vertices": ("insertion_Mops_s", "query_Mops_s", "memory_MB"),
                        "analytics": ("hop1_ops_s", "hop2_ops_s", "bfs_s", "sssp_s", "pr_s", "wcc_s", "tc_s", "bc_s")}[experiment]
            if record["status"] in ("ok", "parse_failed") and any(values.get(key) is None for key in required):
                record["status"] = row["status"] = "parse_failed"
                missing = [key for key in required if values.get(key) is None]
                record["reason"] = "Missing measurements: " + ", ".join(missing)
                issues.append(dict(job=record["name"], missing=missing))
            if experiment == "analytics" and any(value for key, value in values.items() if key.endswith("_timeouts")):
                if record["status"] == "ok":
                    record["status"] = row["status"] = "incomplete_analytics"
                record["reason"] = "One or more graph algorithm repetitions timed out"
                issues.append(dict(job=record["name"], reason=record["reason"]))
        aging, points = {}, []
        database = resolve_evidence(output, meta["database"]) if meta.get("database") else None
        if database and Path(database).is_file():
            try:
                points, aging, terminated = database_series(database)
                if terminated and record["status"] == "ok":
                    record["status"] = "incomplete_workload"
                    issues.append(dict(job=record["name"], termination=terminated))
            except (sqlite3.Error, TypeError, KeyError) as error:
                issues.append(dict(job=record["name"], database_error=str(error)))
                if record["status"] == "ok":
                    record["status"] = "parse_failed"
                    record["reason"] = "Could not read aging measurements: " + str(error)
        if experiment in ("mixed", "delete", "concurrent"):
            if record["status"] == "ok" and (not database or not database.is_file()):
                record["status"] = "parse_failed"
                record["reason"] = "Missing aging SQLite database"
                issues.append(dict(job=record["name"], reason=record["reason"]))
            performed = re.search(r"\[Pipeline\] Operations performed:\s*(\d+)\s*/\s*(\d+)", text)
            complete = performed and int(performed[1]) == int(performed[2]) and int(performed[2]) > 0
            if record["status"] == "ok" and not complete:
                record["status"] = "incomplete_workload"
                record["reason"] = "Missing or mismatched completed operation count"
                issues.append(dict(job=record["name"], reason=record["reason"]))
            if complete and aging and not any(aging.get(key) for key in ("has_terminated_for_timeout", "has_terminated_for_memfp", "has_terminated_deadlocked")):
                final_time = aging.get("completion_time", 0) / 1e6
                if final_time and (not points or points[-1][0] < 100):
                    points.append((100, final_time))
        if experiment == "mixed":
            mixed.extend(dict(system=meta["system"], dataset=meta["dataset"], progress_pct=x, elapsed_s=y,
                              status=record["status"], log=record.get("log", "")) for x, y in points)
            if record["status"] == "ok" and not points:
                record["status"] = "parse_failed"
                issues.append(dict(job=record["name"], missing=["mixed progress measurements"]))
        if experiment == "delete":
            points = delete_series(text)
            deletion.extend(dict(system=meta["system"], dataset=meta["dataset"], progress_pct=x, process_RSS_MB=y,
                                 status=record["status"], log=record.get("log", "")) for x, y in points)
            if record["status"] == "ok" and not points:
                record["status"] = "parse_failed"
                issues.append(dict(job=record["name"], missing=["deletion memory measurements"]))
        if experiment == "concurrent":
            values = concurrent_values(text, meta, aging)
            concurrent.append(dict(system=meta["system"], dataset=meta["dataset"], hop=meta["hop"], axis=meta["axis"],
                                   count=meta["count"], status=record["status"], log=record.get("log", ""), **values))
            if record["status"] == "ok" and any(values.get(key) is None for key in ("read_ops_s", "write_Mops_s")):
                record["status"] = concurrent[-1]["status"] = "parse_failed"
                issues.append(dict(job=record["name"], missing=["concurrent read/write measurements"]))
    write_csv(tables / "figure8.csv", [r for r in rows if r["experiment"] in ("random", "vertices")])
    write_csv(tables / "figure9_mixed.csv", mixed)
    write_csv(tables / "figure9_delete.csv", deletion)
    write_csv(tables / "figure10.csv", [r for r in rows if r["experiment"] == "analytics"])
    write_csv(tables / "figure11.csv", concurrent)
    (tables / "gfe_parse_issues.json").write_text(json.dumps(issues, indent=2) + "\n")
    return {"issues": issues}
