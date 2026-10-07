"""Parse measured standalone results into CSV artifacts.

No paper numbers are embedded here. Missing/failed measurements retain empty
cells and explicit status, with a source log for every row. Units use binary
memory prefixes (upstream's "KB" is Linux KiB). Figure rendering belongs to the
unchanged upstream plotting scripts, invoked separately by the pipeline.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import re

NUMBER = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"


def measured(pattern, text):
    match = re.search(pattern, text)
    if not match:
        return None
    value = float(match.group(1))
    return value if math.isfinite(value) and value >= 0 else None


def parse_transform(text):
    """Return printed fanout change points and measured transformation times."""
    configurations, timings = [], []
    current = None
    for line in text.splitlines():
        match = re.search(r"n\s*=\s*(\d+),\s*a\s*=\s*([\d\s]+)$", line)
        if match:
            current = int(match.group(1))
            fanout = [int(x) for x in match.group(2).split()]
            if len(fanout) == 5 and sum(fanout) == 32:
                item = {"n": current, "fanout": fanout}
                if item not in configurations:
                    configurations.append(item)
        match = re.search(r"Transformation takes\s*" + NUMBER + r"\s*s", line)
        if match and current is not None:
            value = float(match.group(1))
            if math.isfinite(value) and value >= 0:
                timings.append({"n": current, "seconds": value})
    configurations.sort(key=lambda item: item["n"])
    return configurations, timings


def parse_vertex_index(text):
    return [{"method": method,
             "insert_ops_s": measured(r"Throughput for " + method + r" insertion:\s*" + NUMBER, text),
             "query_ops_s": measured(r"Throughput for " + method + r" query:\s*" + NUMBER, text),
             "memory_kib": measured(r"Memory used by " + method + r":\s*" + NUMBER + r"\s*KB", text)}
            for method in ("SORT", "ART")]


def parse_updates(text):
    return {"insert_seconds": measured(r"Time for RadixGraph insertion:\s*" + NUMBER + "s", text),
            "delete_seconds": measured(r"Time for RadixGraph deletion:\s*" + NUMBER + "s", text),
            "memory_kib": measured(r"Memory used by RadixGraph:\s*" + NUMBER + r"\s*KB", text)}


def parse_analytics(text):
    labels = {"two_hop_seconds": "2-hop neighbors", "bfs_seconds": "BFS",
              "sssp_seconds": "SSSP", "bc_seconds": "BC"}
    return {key: measured(re.escape(label) + r": time\s*=\s*" + NUMBER + "s", text)
            for key, label in labels.items()}


def parse_batch(text):
    """Parse RadixGraph's native batch output; keep each batch independent."""
    memory = measured(r"Memory usage:\s*" + NUMBER + r"\s*GB", text)
    times, rates = {}, {}
    for match in re.finditer(r"Batch size:\s*(\d+), average insert time:\s*" + NUMBER
                             + r"s, average delete time:\s*" + NUMBER + "s", text):
        times[int(match.group(1))] = (float(match.group(2)), float(match.group(3)))
    for match in re.finditer(r"Batch size:\s*(\d+), average insert throughput:\s*" + NUMBER
                             + r" ops/s, average delete throughput:\s*" + NUMBER + r" ops/s", text):
        rates[int(match.group(1))] = (float(match.group(2)), float(match.group(3)))
    rows = []
    # The four batch sizes actually present in paper Table 7.
    for batch in (10, 100, 1000, 10000):
        insert, delete = rates.get(batch, (None, None))
        if batch in times:
            insert_time, delete_time = times[batch]
            if insert is None and insert_time > 0:
                insert = batch / insert_time
            if delete is None and delete_time > 0:
                delete = batch / delete_time
        rows.append({"batch_size": batch, "insert_ops_s": insert,
                     "delete_ops_s": delete, "memory_gib": memory})
    return rows


def _resolve(output, path):
    path = Path(path)
    if not path.is_absolute():
        return output / path
    # An exported Docker result directory can be rerendered at another path.
    # Always use its retained evidence, even if the original log still exists.
    for marker in ("studies", "logs"):
        if marker in path.parts:
            return output.joinpath(*path.parts[path.parts.index(marker):])
    return path


def _read(output, path):
    target = _resolve(output, path)
    return target.read_text(errors="replace") if target.is_file() else ""


def _source(output, path):
    target = _resolve(output, path)
    try:
        return str(target.relative_to(output))
    except ValueError:
        return str(target)


def _status(record, row, fields):
    if record.get("status") != "ok":
        return record.get("status", "missing")
    return "ok" if all(row.get(key) is not None for key in fields) else "parse_error"


def _write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def render_study_reports(output, records=None):
    """Regenerate CSV artifacts from saved logs only."""
    output = Path(output).resolve()
    if records is None:
        index = output / "studies" / "records.json"
        records = json.loads(index.read_text()) if index.is_file() else []
    tables = output / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    table5, update_rows, analytics_rows, table7 = [], [], [], []
    configurations, transformations, memory_points, workload_points, memory_costs = [], [], [], [], []
    issues = []

    for record in records:
        meta = record.get("metadata", {})
        artifact, kind = meta.get("artifact"), meta.get("kind")
        text = _read(output, record.get("log", ""))
        source = _source(output, record.get("log", ""))
        common = {"source_log": source}
        if artifact == "table5":
            for data in parse_vertex_index(text):
                row = {**common, "n": meta["n"], "bits": meta["bits"], **data}
                row["status"] = _status(record, row, ("insert_ops_s", "query_ops_s", "memory_kib"))
                table5.append(row)
        elif artifact == "table6":
            data = parse_updates(text) if kind == "updates" else parse_analytics(text)
            row = {**common, "dataset": meta["dataset"], "variant": meta["variant"], **data}
            row["status"] = _status(record, row, data.keys())
            (update_rows if kind == "updates" else analytics_rows).append(row)
            if row["status"] == "parse_error":
                issues.append({"artifact": "table6", **common, "status": "parse_error",
                               "detail": f"Missing expected {kind} measurements for {meta['variant']} on {meta['dataset']}."})
        elif artifact == "table7" and meta.get("method") == "RadixGraph":
            for data in parse_batch(text):
                row = {**common, "dataset": meta["dataset"], "method": "RadixGraph", **data}
                row["status"] = _status(record, row, ("insert_ops_s", "delete_ops_s", "memory_gib"))
                table7.append(row)
        elif artifact == "figure12" and kind == "transformation":
            config, times = parse_transform(text)
            configurations.extend({**item, **common} for item in config)
            transformations.extend({**item, **common} for item in times)
            work = _resolve(output, record.get("workdir", ""))
            memory_file = work / "memory_log.txt"
            if not memory_file.is_file() and source:
                memory_file = output / "studies" / "work" / record.get("name", "figure12-transformation") / "memory_log.txt"
            if memory_file.is_file():
                for line in memory_file.read_text().splitlines():
                    try:
                        n, byte_count = (int(x) for x in line.split())
                        memory_points.append({"n": n, "memory_bytes": byte_count,
                                              "source_log": _source(output, memory_file)})
                    except ValueError:
                        issues.append({"artifact": "figure12", "source_log": str(memory_file),
                                       "status": "parse_error", "detail": f"Invalid memory row: {line}"})
            if not config or len(times) != max(0, len(config) - 1) or not memory_points:
                issues.append({"artifact": "figure12", **common, "status": "parse_error",
                               "detail": "Missing fanout, transformation timing, or memory data."})
        elif artifact == "figure12" and kind == "memory":
            row = {**common, "n": meta["n"], "variant": meta["variant"],
                   "fanout": " ".join(map(str, meta.get("fanout") or [])),
                   "memory_bytes": measured(r"Allocated space of your Trie:\s*" + NUMBER + r"\s*bytes", text)}
            row["status"] = _status(record, row, ("memory_bytes",))
            memory_costs.append(row)
        elif artifact == "figure13":
            found = {"SORT": {}, "vEB": {}}
            for match in re.finditer(r"(SORT|vEB): memory after (\d+) insertions is (\d+) bytes", text):
                found[match.group(1)][int(match.group(2))] = int(match.group(3))
            for method, points in found.items():
                for n, byte_count in sorted(points.items()):
                    workload_points.append({**common, "workload": meta["workload"],
                                            "method": method, "n": n,
                                            "fraction": n / meta["n"], "memory_bytes": byte_count,
                                            "status": record.get("status", "missing")})
            if any(len(points) != 10 or max(points, default=0) != meta["n"] for points in found.values()):
                issues.append({"artifact": "figure13", **common, "status": "parse_error",
                               "detail": "Expected ten memory samples ending at n for both SORT and vEB."})
        if record.get("status") != "ok":
            issues.append({"artifact": artifact, **common, "status": record.get("status"),
                           "detail": record.get("name", "")})

    fields5 = ["n", "bits", "method", "insert_ops_s", "query_ops_s", "memory_kib", "status", "source_log"]
    _write_csv(tables / "table5.csv", table5, fields5)
    fields_updates = ["dataset", "variant", "insert_seconds", "delete_seconds", "memory_kib", "status", "source_log"]
    fields_analytics = ["dataset", "variant", "two_hop_seconds", "bfs_seconds", "sssp_seconds", "bc_seconds", "status", "source_log"]
    _write_csv(tables / "table6_updates.csv", update_rows, fields_updates)
    _write_csv(tables / "table6_analytics.csv", analytics_rows, fields_analytics)
    datasets = list(dict.fromkeys(row["dataset"] for row in update_rows + analytics_rows))
    table6 = []
    ratio_fields = ["art_sort_insert_ratio", "art_sort_delete_ratio", "art_sort_memory_ratio",
                    "no_chain_two_hop_ratio", "no_chain_bfs_ratio", "no_chain_sssp_ratio", "no_chain_bc_ratio"]
    for dataset in datasets:
        up = {row["variant"]: row for row in update_rows if row["dataset"] == dataset}
        an = {row["variant"]: row for row in analytics_rows if row["dataset"] == dataset}
        row = {"dataset": dataset}
        for field, result_key in (("insert_seconds", "art_sort_insert_ratio"),
                                  ("delete_seconds", "art_sort_delete_ratio"),
                                  ("memory_kib", "art_sort_memory_ratio")):
            base, alt = up.get("sort", {}), up.get("art", {})
            row[result_key] = _ratio(alt.get(field), base.get(field)) if base.get("status") == alt.get("status") == "ok" else None
        for field, result_key in (("two_hop_seconds", "no_chain_two_hop_ratio"),
                                  ("bfs_seconds", "no_chain_bfs_ratio"),
                                  ("sssp_seconds", "no_chain_sssp_ratio"),
                                  ("bc_seconds", "no_chain_bc_ratio")):
            base, alt = an.get("sort", {}), an.get("sort-no-chain", {})
            row[result_key] = _ratio(alt.get(field), base.get(field)) if base.get("status") == alt.get("status") == "ok" else None
        row["status"] = "ok" if all(row.get(key) is not None for key in ratio_fields) else "incomplete"
        row["source_logs"] = " ; ".join(item["source_log"] for item in list(up.values()) + list(an.values()))
        table6.append(row)
    fields6 = ["dataset", *ratio_fields, "status", "source_logs"]
    _write_csv(tables / "table6.csv", table6, fields6)
    fields7 = ["dataset", "method", "batch_size", "insert_ops_s", "delete_ops_s", "memory_gib", "status", "source_log"]
    _write_csv(tables / "table7_radixgraph.csv", table7, fields7)
    baseline_file = tables / "table7_baselines.csv"
    if baseline_file.is_file():
        with baseline_file.open(newline="") as handle:
            table7.extend(csv.DictReader(handle))
    _write_csv(tables / "table7.csv", table7, fields7)
    fanout_rows = [{**row, "fanout": " ".join(map(str, row["fanout"]))} for row in configurations]
    _write_csv(tables / "figure12_fanouts.csv", fanout_rows, ["n", "fanout", "source_log"])
    _write_csv(tables / "figure12_memory_costs.csv", memory_costs, ["n", "variant", "fanout", "memory_bytes", "status", "source_log"])
    _write_csv(tables / "figure12_memory_footprint.csv", memory_points, ["n", "memory_bytes", "source_log"])
    _write_csv(tables / "figure12_transformations.csv", transformations, ["n", "seconds", "source_log"])
    _write_csv(tables / "figure13_workloads.csv", workload_points, ["workload", "method", "n", "fraction", "memory_bytes", "status", "source_log"])
    for name in ("table5", "table6", "table7", "figure12_fanouts", "figure12_memory_costs", "figure12_transformations"):
        (tables / f"{name}.md").unlink(missing_ok=True)
    for label, rows in (("table5", table5), ("table6", table6), ("table7", table7), ("figure12", memory_costs)):
        for row in rows:
            if row.get("status") != "ok":
                issues.append({"artifact": label, "source_log": row.get("source_log", row.get("source_logs", "")),
                               "status": row.get("status"), "detail": "Missing or unsuccessful measurement; see result row."})
    report = {"figures": [], "issues": issues, "rows": {"table5": len(table5), "table6": len(table6), "table7": len(table7)}}
    (output / "studies").mkdir(parents=True, exist_ok=True)
    (output / "studies" / "report_status.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Existing reproduction output directory")
    args = parser.parse_args()
    report = render_study_reports(args.output)
    print(json.dumps(report, indent=2))
