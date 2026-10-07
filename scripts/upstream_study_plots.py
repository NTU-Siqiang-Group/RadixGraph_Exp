"""Adapt retained study measurements to the unchanged upstream renderers.

This module stages input files and invokes the original plotting code. It does
not define drawing instructions or substitute the paper's example numbers for
measurements from the current run.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import shutil


SORT_INVOKER = '''"""Invoke original SORT plotting functions without its fixed example data."""
import ast
import json
from pathlib import Path
import sys

request = json.load(sys.stdin)
source = Path(request["source"])
tree = ast.parse(source.read_text(), filename=str(source))
# The upstream module ends with two examples containing fixed paper numbers.
# Execute its unchanged setup and function definitions, stopping before those
# examples. This preserves the original font setup, constants, and renderers.
examples = [i for i, node in enumerate(tree.body)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in ("plot1", "plot2")]
if len(examples) != 2 or examples[1] != examples[0] + 1:
    raise SystemExit("Upstream SORT example layout changed; inspect original script")
namespace = {"__name__": "upstream_sort_functions", "__file__": str(source)}
definitions = ast.Module(body=tree.body[:examples[0]], type_ignores=[])
exec(compile(definitions, str(source), "exec"), namespace)
function = request["function"]
if function not in ("plot1", "plot2"):
    raise SystemExit("Unknown original SORT renderer")
namespace[function](*request["arguments"], request["output"])
'''


def _rows(path):
    if not path.is_file():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _number(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Measurement must be a finite nonnegative number")
    return result


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(output, path):
    path = Path(path)
    if not path.is_absolute():
        return output / path
    for marker in ("studies", "logs"):
        if marker in path.parts:
            return output.joinpath(*path.parts[path.parts.index(marker):])
    return path


def _records(output):
    path = output / "studies" / "records.json"
    return json.loads(path.read_text()) if path.is_file() else []


def render_study_plots(output, radix, gfe, profile, runner):
    """Return metadata for original renderers run on measured study inputs.

    ``runner(script, cwd, input_text=None)`` executes a Python script and returns
    a dictionary containing at least ``status``. CSV export happens in
    ``study_reports.py`` before this adapter is called.
    """
    output, radix, gfe = Path(output).resolve(), Path(radix), Path(gfe)
    tables = output / "tables"
    records = _records(output)
    if not records and not any(_rows(tables / filename) for filename in (
            "figure12_fanouts.csv", "figure12_memory_costs.csv", "figure12_memory_footprint.csv",
            "figure12_transformations.csv", "figure13_workloads.csv")):
        return {"plots": [], "issues": []}
    root = output / "upstream" / "studies"
    root.mkdir(parents=True, exist_ok=True)
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    originals = figures / "upstream"
    originals.mkdir(parents=True, exist_ok=True)
    plots, issues = [], []

    def skipped(artifact, reason):
        plots.append({"artifact": artifact, "status": "skipped", "reason": reason,
                      "figures": []})

    def render(artifact, script, work, generated, original_name, alias, request=None,
               source=None, notes=None):
        source = source or script
        record = {"artifact": artifact, "source_script": str(source),
                  "status": "failed", "figures": [], "notes": notes or []}
        if not source.is_file():
            record["reason"] = f"Original plotting script is missing: {source}"
        else:
            record["source_sha256"] = _sha256(source)
            try:
                generated.unlink(missing_ok=True)
                result = runner(script, work, input_text=json.dumps(request) if request else None)
                record["execution"] = result
                record["source_unchanged"] = source.is_file() and _sha256(source) == record["source_sha256"]
                if not record["source_unchanged"]:
                    raise RuntimeError("Original plotting source changed during execution")
                record["status"] = result.get("status", "failed")
                if record["status"] == "ok" and generated.is_file():
                    paths = (originals / original_name, figures / alias)
                    for path in paths:
                        shutil.copyfile(generated, path)
                    record["figures"] = [str(path.relative_to(output)) for path in paths]
                else:
                    record["status"] = "failed"
                    record["reason"] = result.get("reason") or f"Original renderer did not produce {generated.name}"
            except Exception as error:
                record["status"] = "failed"
                record["source_unchanged"] = source.is_file() and _sha256(source) == record["source_sha256"]
                record["reason"] = str(error)
        plots.append(record)
        if record["status"] == "failed":
            issues.append({"artifact": artifact, "status": "failed", "detail": record.get("reason", "Original renderer failed")})

    sort_source = gfe / "scripts" / "plot_sort.py"
    invoker = root / "invoke_sort.py"
    invoker.write_text(SORT_INVOKER)
    fanouts = _rows(tables / "figure12_fanouts.csv")
    transform = next((item for item in records
                      if item.get("metadata", {}).get("artifact") == "figure12"
                      and item.get("metadata", {}).get("kind") == "transformation"), None)
    if fanouts and transform and transform.get("status") == "ok":
        try:
            fanouts = sorted(fanouts, key=lambda row: int(row["n"]))
            widths = [[int(value) for value in row["fanout"].split()] for row in fanouts]
            if any(len(values) != 5 or sum(values) != 32 for values in widths):
                raise ValueError("Expected five measured fanout widths summing to 32")
            work = root / "figure12_a"
            work.mkdir(parents=True, exist_ok=True)
            request = {"source": str(sort_source), "function": "plot1",
                       "arguments": [[int(row["n"]) for row in fanouts],
                                     [[values[layer] for values in widths] for layer in range(5)]],
                       "output": "figures/sort.pdf"}
            render("figure12_a", invoker, work, work / "figures" / "sort.pdf",
                   "sort.pdf", "figure12_a.pdf", request, sort_source,
                   ["Original plot_sort.plot1 consumes this run's measured fanouts; its hardcoded example calls are not executed."])
        except (ValueError, KeyError) as error:
            skipped("figure12_a", f"Measured fanouts are incomplete: {error}")
    else:
        skipped("figure12_a", "No successful measured fanout study is available; see tables/figure12_fanouts.csv.")

    costs = _rows(tables / "figure12_memory_costs.csv")
    if costs and all(row.get("status") == "ok" for row in costs):
        try:
            counts = sorted({int(row["n"]) for row in costs})
            values = {(int(row["n"]), row["variant"]): _number(row["memory_bytes"]) / 2**20 for row in costs}
            data = [[values[n, method] for n in counts] for method in ("Updated", "Trailing", "vEB")]
            labels = [rf"$10^{{{int(math.log10(n))}}}$" if n > 0 and 10**int(math.log10(n)) == n else str(n) for n in counts]
            work = root / "figure12_b"
            work.mkdir(parents=True, exist_ok=True)
            request = {"source": str(sort_source), "function": "plot2",
                       "arguments": [labels, data], "output": "figures/memory_change.pdf"}
            render("figure12_b", invoker, work, work / "figures" / "memory_change.pdf",
                   "memory_change.pdf", "figure12_b.pdf", request, sort_source,
                   ["Original plot_sort.plot2 consumes this run's measured memory costs, converted from bytes to MiB; its hardcoded examples are not executed."])
        except (ValueError, KeyError) as error:
            skipped("figure12_b", f"Measured memory costs are incomplete: {error}")
    else:
        skipped("figure12_b", "No complete successful measured memory study is available; see tables/figure12_memory_costs.csv.")

    if profile == "smoke":
        skipped("figure12_c", "The original plot_memory_log.py fixes its x-axis to the paper's ten-million-vertex range. Smoke measurements are retained in tables/figure12_memory_footprint.csv.")
    elif transform and transform.get("status") == "ok":
        work = root / "figure12_c"
        work.mkdir(parents=True, exist_ok=True)
        native = _resolve(output, transform.get("workdir", "")) / "memory_log.txt"
        if not native.is_file():
            skipped("figure12_c", "Retained native memory_log.txt is unavailable; see tables/figure12_memory_footprint.csv.")
        else:
            shutil.copyfile(native, work / "memory_log.txt")
            points = [line.split() for line in native.read_text().splitlines() if line.strip()]
            notes = ["Original plot_memory_log.py drops the first sample and uses its original fixed paper x-axis labels."]
            if points and points[0][0] != "0":
                notes.append("The benchmark's retained log starts after the initial insertion interval; the original renderer's fixed x-axis labels can differ by one sampling interval. The CSV preserves actual measured vertex counts.")
            render("figure12_c", radix / "plot_memory_log.py", work,
                   work / "figures" / "memory_log.pdf", "memory_log.pdf", "figure12_c.pdf", notes=notes)
    else:
        skipped("figure12_c", "No successful measured memory footprint study is available.")

    workload_rows = _rows(tables / "figure13_workloads.csv")
    work = root / "figure13"
    work.mkdir(parents=True, exist_ok=True)
    valid = True
    for code in ("u", "s", "h"):
        rows = [row for row in workload_rows if row.get("workload") == code]
        points = {method: sorted((row for row in rows if row.get("method") == method),
                                 key=lambda row: int(row["n"])) for method in ("SORT", "vEB")}
        if any(len(items) != 10 or any(item.get("status") != "ok" for item in items) for items in points.values()):
            valid = False
            break
        try:
            if [row["n"] for row in points["SORT"]] != [row["n"] for row in points["vEB"]]:
                raise ValueError("SORT and vEB measured insertion counts differ")
            if any(not math.isclose(float(row["fraction"]), (index + 1) / 10) for items in points.values() for index, row in enumerate(items)):
                raise ValueError("Original workload renderer expects ten samples at ten-percent intervals")
            native_record = next((record for record in records
                                  if record.get("metadata", {}).get("artifact") == "figure13"
                                  and record.get("metadata", {}).get("workload") == code), None)
            native = _resolve(output, native_record.get("workdir", "")) / f"workload_{code}_log.txt" if native_record else None
            target = work / f"workload_{code}_log.txt"
            if native and native.is_file():
                shutil.copyfile(native, target)
            else:
                # The original parser ignores column one; retain actual n there.
                target.write_text("".join(f"{left['n']} {int(_number(left['memory_bytes']))} {int(_number(right['memory_bytes']))}\n"
                                          for left, right in zip(points["SORT"], points["vEB"])))
        except (ValueError, KeyError):
            valid = False
            break
    if valid:
        render("figure13", radix / "plot_workload_log.py", work,
               work / "figures" / "workload_log.pdf", "workload_log.pdf", "figure13.pdf",
               notes=["Original plot_workload_log.py consumes the three retained measured workload logs."])
    else:
        skipped("figure13", "Three complete successful SORT/vEB workload studies are required; see tables/figure13_workloads.csv.")
    return {"plots": plots, "issues": issues}
