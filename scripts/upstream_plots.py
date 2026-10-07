"""Stage measured inputs and invoke the original plotting scripts unchanged."""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from gfe_reports import DATASETS, LABELS, METHODS, resolve_evidence


def write_plot_csv(path, records, experiment, metric, scale=1):
    """The original scripts require a final empty column after six datasets."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Method", *LABELS, ""])
        for system, label in METHODS.items():
            values = []
            for dataset in DATASETS:
                row = next((r for r in records if r["system"] == system
                            and r["dataset"] == dataset and r["experiment"] == experiment), {})
                value = row.get(metric) if row.get("status") == "ok" else None
                try:
                    value = float(value) * scale
                    if not math.isfinite(value):
                        value = None
                except (TypeError, ValueError):
                    value = None
                values.append(value if value is not None else "NaN")
            writer.writerow([label, *values, ""])


def run_original(script, cwd, input_text=None, arguments=None):
    script, cwd = Path(script), Path(cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    log = cwd / (script.stem + ".log")
    result = dict(script=str(script), log=str(log), status="failed", exit_code=None)
    if not script.is_file():
        result["reason"] = "Original plotting script is missing: " + str(script)
        log.write_text(result["reason"] + "\n")
        return result
    before = hashlib.sha256(script.read_bytes()).hexdigest()
    result["source_sha256"] = before
    command = [sys.executable, str(script), *(str(a) for a in (arguments or []))]
    result["command"] = command
    try:
        with log.open("w") as stream:
            completed = subprocess.run(command, cwd=cwd, input=input_text, text=True,
                                       stdout=stream, stderr=subprocess.STDOUT, timeout=300,
                                       env={**os.environ, "MPLBACKEND": "Agg"})
        result.update(exit_code=completed.returncode,
                      status="ok" if completed.returncode == 0 else "failed")
    except (OSError, subprocess.TimeoutExpired) as error:
        with log.open("a") as stream:
            stream.write(str(error) + "\n")
        result["reason"] = str(error)
    result["source_unchanged"] = hashlib.sha256(script.read_bytes()).hexdigest() == before
    if not result["source_unchanged"]:
        result.update(status="failed", reason="Original plotting source changed during execution")
    return result


def _read_csv(path):
    if not path.is_file():
        return []
    with path.open() as stream:
        return list(csv.DictReader(stream))


def _copy_figure(output, work, source_name, alias):
    source = work / "figures" / source_name
    if not source.is_file():
        return []
    upstream = output / "figures" / "upstream"
    upstream.mkdir(parents=True, exist_ok=True)
    original = upstream / source_name
    canonical = output / "figures" / (alias + ".pdf")
    shutil.copy2(source, original)
    shutil.copy2(source, canonical)
    return [str(p.relative_to(output)) for p in (original, canonical)]


def _plot(output, gfe, work, script, artifact, filename, input_text=None, notes=None):
    execution = run_original(gfe / "scripts" / script, work, input_text)
    figures = _copy_figure(output, work, filename, artifact) if execution["status"] == "ok" else []
    record = dict(artifact=artifact, status=execution["status"], execution=execution,
                  figures=figures, notes=notes or [])
    if record["status"] == "ok" and not figures:
        record.update(status="failed", reason="Original script did not produce " + filename)
    return record


def _stage_raw_gfe(output, records, work):
    omissions = []
    for system in METHODS:
        for folder in ("mixed", "delete-only", "concurrent-1-hop", "concurrent-2-hop"):
            (work / "results" / system / folder).mkdir(parents=True, exist_ok=True)
    for record in records:
        meta = record.get("metadata", {})
        if meta.get("suite") != "gfe" or record["status"] != "ok":
            continue
        system, dataset, experiment = meta["system"], meta["dataset"], meta["experiment"]
        base = work / "results" / system
        if experiment == "mixed":
            source = resolve_evidence(output, meta["database"])
            try:
                with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
                    count = connection.execute("SELECT count(*) FROM aging_intermediate_throughput3").fetchone()[0]
                if count < 2:
                    raise ValueError("fewer than two one-second samples")
                shutil.copy2(source, base / "mixed" / (dataset + ".sqlite3"))
            except (sqlite3.Error, OSError, ValueError) as error:
                omissions.append(dict(job=record["name"], reason=str(error)))
        elif experiment == "delete":
            shutil.copy2(resolve_evidence(output, record["log"]), base / "delete-only" / dataset)
        elif experiment == "concurrent":
            # Avoid any other digits: the original parser identifies thread
            # counts by searching for 4,8,16,32 anywhere in each filename.
            name = f"dota-{meta['count']}-{meta['axis']}-threads"
            shutil.copy2(resolve_evidence(output, record["log"]),
                         base / f"concurrent-{meta['hop']}-hop" / name)
    return omissions


def _archive_legacy_figures(output):
    # Previously generated custom figures must not masquerade as upstream
    # output when an existing run is regenerated after this pipeline change.
    marker = output / "plotting" / "manifest.json"
    figures = output / "figures"
    if marker.exists():
        # These are derived files owned by this adapter, not raw evidence.
        previous = json.loads(marker.read_text())
        for plot in previous.get("plots", []):
            for name in plot.get("figures", []):
                relative = Path(name)
                if relative.parts and relative.parts[0] == "figures" and ".." not in relative.parts:
                    (output / relative).unlink(missing_ok=True)
        return
    if not figures.exists():
        return
    for source in figures.glob("figure*"):
        if source.is_file():
            archive = output / "legacy-figures"
            archive.mkdir(exist_ok=True)
            destination = archive / source.name
            if destination.exists():
                destination = archive / (source.stem + "-" + hashlib.sha256(source.read_bytes()).hexdigest()[:12] + source.suffix)
            shutil.move(str(source), destination)


def render_original_plots(output, records, gfe, radix, profile):
    output, gfe, radix = map(Path, (output, gfe, radix))
    _archive_legacy_figures(output)
    work = output / "plotting" / "gfe"
    work.mkdir(parents=True, exist_ok=True)
    # Rebuild staged inputs, so removed evidence cannot survive regeneration.
    for folder in ("csv", "results", "figures"):
        path = work / folder
        if path.exists():
            shutil.rmtree(path)
    rows = _read_csv(output / "tables" / "figure8.csv") + _read_csv(output / "tables" / "figure10.csv")
    plots = []
    specs = [("edge_insertion", "random", "insertion_Mops_s", 1),
             ("edge_deletion", "random", "deletion_Mops_s", 1),
             ("memory", "random", "memory_MB", 1),
             ("vertex_insertion", "vertices", "insertion_Mops_s", 1),
             ("vertex_query", "vertices", "query_Mops_s", 1),
             ("vertex_memory", "vertices", "memory_MB", 1),
             ("get_neighbor_throughput", "analytics", "hop1_ops_s", 1),
             ("two_hop_throughput", "analytics", "hop2_ops_s", 1)]
    specs += [(name + "_latency", "analytics", metric + "_s", 1000)
              for name, metric in (("bfs", "bfs"), ("sssp", "sssp"), ("pr", "pr"),
                                   ("wcc", "wcc"), ("lcc", "tc"), ("bc", "bc"))]
    for filename, experiment, metric, scale in specs:
        write_plot_csv(work / "csv" / (filename + ".csv"), rows, experiment, metric, scale)
    if any(r["experiment"] in ("random", "vertices") for r in rows):
        plots.append(_plot(output, gfe, work, "plot_all_1.py", "figure8", "combined_plots1.pdf"))
    if any(r["experiment"] == "analytics" for r in rows):
        plots.append(_plot(output, gfe, work, "plot_all_3.py", "figure10", "combined_plots3.pdf"))
    omissions = _stage_raw_gfe(output, records, work)
    gfe_records = [r for r in records if r.get("metadata", {}).get("suite") == "gfe"]
    if any(r["metadata"]["experiment"] in ("mixed", "delete") for r in gfe_records):
        plots.append(_plot(output, gfe, work, "plot_all_2.py", "figure9", "combined_plots2.pdf", notes=[
            "Original progress calculations are retained, including the GTX 20%-workload assumption and deletion sample spacing.",
            "Mixed SQLite files without two one-second samples are omitted; their measured tables remain available.",
            {"omitted_mixed_inputs": omissions}]))
    concurrent = [r for r in gfe_records if r["metadata"]["experiment"] == "concurrent"]
    if concurrent:
        slots = {(r["metadata"]["system"], r["metadata"]["hop"], r["metadata"]["axis"], r["metadata"]["count"])
                 for r in concurrent if r["status"] == "ok"}
        required = {(system, hop, axis, count) for system in ("gtx", "radixgraph")
                    for hop, axis in ((1, "read"), (2, "read"), (2, "write")) for count in (4, 8, 16, 32)}
        if profile == "paper" and required <= slots:
            plots.append(_plot(output, gfe, work, "plot_concurrent.py", "figure11", "combined_plots4.pdf",
                               "concurrent-1-hop\nconcurrent-2-hop\n", notes=[
                                   "Original constants are retained: 317727 one-hop IDs and 508703130 update operations.",
                                   "The measurement CSV uses the actual counts and remains the authoritative numeric export."]))
        else:
            plots.append(dict(artifact="figure11", status="skipped", figures=[], reason=
                              "The original renderer requires both systems at 4/8/16/32 threads and paper operation counts. See tables/figure11.csv."))
    from upstream_study_plots import render_study_plots
    studies = render_study_plots(output, radix, gfe, profile, run_original)
    plots.extend(studies["plots"])
    result = dict(renderer="original upstream scripts", profile=profile, plots=plots,
                  issues=[p for p in plots if p["status"] == "failed"] + studies.get("issues", []))
    (output / "plotting" / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
