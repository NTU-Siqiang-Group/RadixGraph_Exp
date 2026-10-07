"""Format measured CSV exports using the paper's table layouts.

Measurement CSVs are never rewritten, and numbers are never substituted from
the paper. Formatting stays in memory; embedded HTML describes merged headers
and row labels. The command-line entry point exports PDFs containing only the
table grid, without persisting intermediate Markdown tables.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
from html import escape
import math
from pathlib import Path
import sys


DATASET_ORDER = ("lj", "orkut", "dota", "g24", "u24", "twitter")
DATASET_LABELS = {"lj": "LJ", "orkut": "Orkut", "dota": "Dota",
                  "g24": "G24", "u24": "U24", "twitter": "Twitter"}
DATASET_ALIASES = {
    "lj": "lj", "livejournal": "lj", "com-lj.ungraph": "lj",
    "orkut": "orkut", "com-orkut.ungraph": "orkut",
    "dota": "dota", "dota-league": "dota", "dotaleague": "dota",
    "g24": "g24", "graph500-24": "g24",
    "u24": "u24", "uniform-24": "u24",
    "twitter": "twitter", "twitter-2010": "twitter",
}
METHODS = ("RadixGraph", "Terrace", "Aspen", "CPAM")
TABLE7_DATASETS = ("lj", "orkut", "twitter")
BATCHES = (10, 100, 1000, 10000)
RATIO_FIELDS = ("art_sort_insert_ratio", "art_sort_delete_ratio",
                "art_sort_memory_ratio", "no_chain_two_hop_ratio",
                "no_chain_bfs_ratio", "no_chain_sssp_ratio", "no_chain_bc_ratio")
MISSING = "—"


def _read_csv(path):
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _integer(value):
    try:
        number = Decimal(str(value).strip())
        if number.is_finite() and number >= 0 and number == number.to_integral_value():
            return int(number)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        pass
    return None


def _ok(row):
    return str(row.get("status", "")).strip().lower() == "ok"


def _dataset(value):
    value = str(value or "").strip()
    stem = value.rsplit("/", 1)[-1]
    for suffix in (".properties", ".vertices.el", ".e", ".v"):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return DATASET_ALIASES.get(stem.lower(), value)


def _label(dataset):
    return DATASET_LABELS.get(dataset, dataset)


def _dataset_sort(dataset):
    return (DATASET_ORDER.index(dataset), "") if dataset in DATASET_ORDER else (len(DATASET_ORDER), dataset.casefold())


def _method(value):
    value = str(value or "").strip()
    return next((method for method in METHODS if method.casefold() == value.casefold()), value)


def _scientific(number, decimals):
    mantissa, exponent = f"{number:.{decimals}E}".split("E")
    return f"{mantissa}E{int(exponent)}"


def _n_label(n):
    if n >= 10 and str(n)[0] == "1" and set(str(n)[1:]) == {"0"}:
        return f"10<sup>{len(str(n)) - 1}</sup>"
    return str(n)


def _cell(value, formatter, bold=False):
    if value is None:
        return f"<td>{MISSING}</td>"
    content = formatter(value)
    if bold:
        content = f"<strong>{content}</strong>"
    return f"<td>{content}</td>"


def _best(values, minimum=False):
    available = [value for value in values if value is not None]
    return (min(available) if minimum else max(available)) if len(available) >= 2 else None


def _measurement(rows, field, notes, description, statuses=("ok",)):
    values = {_number(row.get(field)) for row in rows
              if str(row.get("status", "")).strip().lower() in statuses}
    values.discard(None)
    if len(values) > 1:
        notes.append(f"Conflicting successful measurements for {escape(description)}; the cell is unavailable. See the CSV for each recorded value.")
        return None
    return next(iter(values), None)


def _common_note(csv_name):
    return (f"Numbers are rounded only in the formatted table; original values, statuses, and source-log references "
            f"remain in [{csv_name}]({csv_name}). {MISSING} indicates a missing, unsuccessful, non-finite, or conflicting measurement.")


def _finish(title, body, notes):
    # Deduplicate notes when multiple cells share the same cause.
    lines = [title, *body, *dict.fromkeys(notes)]
    return "\n\n".join(lines) + "\n"


def _table5(tables):
    rows = _read_csv(tables / "table5.csv")
    notes = [_common_note("table5.csv"),
             "Insertion and query throughputs are operations/second. Memory (KB) uses the original log's units: KiB of measured RSS delta. When both SORT/ART values are available, bold marks the higher throughput or lower memory."]
    index = {}
    for row in rows:
        n, bits = _integer(row.get("n")), _integer(row.get("bits"))
        method = str(row.get("method", "")).strip().upper()
        if n is None or bits not in (24, 32) or method not in ("SORT", "ART"):
            notes.append("Rows outside the paper's SORT/ART and 24/32-bit configurations are retained in [table5.csv](table5.csv).")
            continue
        index.setdefault((n, bits, method), []).append(row)
    if not index:
        return _finish("Table 5: SORT and ART vertex indexes", ["No measured rows are available."], notes)
    lines = ["<table>", "<thead>", "<tr><th rowspan=\"3\">n</th><th colspan=\"4\">Insertion</th><th colspan=\"4\">Query</th><th colspan=\"4\">Memory (KB)</th></tr>",
             "<tr>" + "".join(f"<th colspan=\"2\">u = 2<sup>{bits}</sup></th>" for _ in range(3) for bits in (24, 32)) + "</tr>",
             "<tr>" + "<th>SORT</th><th>ART</th>" * 6 + "</tr>", "</thead>", "<tbody>"]
    for n in sorted({key[0] for key in index}):
        cells = [f"<th>{_n_label(n)}</th>"]
        for field in ("insert_ops_s", "query_ops_s", "memory_kib"):
            for bits in (24, 32):
                values = [_measurement(index.get((n, bits, method), []), field, notes,
                                       f"n={n}, bits={bits}, {method}, {field}") for method in ("SORT", "ART")]
                best = _best(values, minimum=field == "memory_kib")
                cells.extend(_cell(value, lambda number: _scientific(number, 1),
                                   value is not None and value == best) for value in values)
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.extend(("</tbody>", "</table>"))
    return _finish("Table 5: SORT and ART vertex indexes", ["\n".join(lines)], notes)


def _table6(tables):
    rows = _read_csv(tables / "table6.csv")
    notes = [_common_note("table6.csv"),
             "ART/SORT ratios compare elapsed insertion/deletion time and measured RSS delta. Slowdown w/o edge chain is no-edge-chain/edge-chain elapsed time. Individual valid ratios remain visible when the overall row is incomplete. Raw measurements are in [table6_updates.csv](table6_updates.csv) and [table6_analytics.csv](table6_analytics.csv)."]
    index = {}
    for row in rows:
        dataset = _dataset(row.get("dataset"))
        if dataset:
            index.setdefault(dataset, []).append(row)
    if not index:
        return _finish("Table 6: RadixGraph ablations", ["No measured rows are available."], notes)
    lines = ["<table>", "<thead>", "<tr><th rowspan=\"2\">Graphs</th><th colspan=\"3\">ART v.s. SORT</th><th colspan=\"4\">Slowdown w/o edge chain</th></tr>",
             "<tr><th>Insert</th><th>Delete</th><th>Memory</th><th>2-hop</th><th>BFS</th><th>SSSP</th><th>BC</th></tr>", "</thead>", "<tbody>"]
    for dataset in sorted(index, key=_dataset_sort):
        cells = [f"<th>{escape(dataset)}</th>"]
        for field in RATIO_FIELDS:
            value = _measurement(index[dataset], field, notes, f"{_label(dataset)}, {field}",
                                 statuses=("ok", "incomplete"))
            cells.append(_cell(value, lambda number: f"{number:.2f}×"))
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.extend(("</tbody>", "</table>"))
    return _finish("Table 6: RadixGraph ablations", ["\n".join(lines)], notes)


def _batch_table(index, datasets, methods, notes):
    lines = ["<table>", "<thead>", "<tr><th rowspan=\"2\"></th><th rowspan=\"2\">Batch</th>" +
             "".join(f"<th colspan=\"{len(methods)}\">{escape(_label(dataset))}</th>" for dataset in datasets) + "</tr>",
             "<tr>" + "".join(f"<th>{escape(method)}</th>" for _ in datasets for method in methods) + "</tr>", "</thead>", "<tbody>"]
    for field, metric in (("insert_ops_s", "Insertion<br>throughput"),
                          ("delete_ops_s", "Deletion<br>throughput")):
        for position, batch in enumerate(BATCHES):
            cells = [f"<th rowspan=\"4\">{metric}</th>"] if position == 0 else []
            cells.append(f"<th>{batch if batch == 10 else _n_label(batch)}</th>")
            for dataset in datasets:
                values = [_measurement([row for row in index.get((dataset, method), [])
                                        if _integer(row.get("batch_size")) == batch], field, notes,
                                       f"{_label(dataset)}, {method}, batch={batch}, {field}") for method in methods]
                best = _best(values)
                cells.extend(_cell(value, lambda number: _scientific(number, 2),
                                   value is not None and value == best) for value in values)
            lines.append("<tr>" + "".join(cells) + "</tr>")
    cells = ["<th>Memory</th><th>/</th>"]
    for dataset in datasets:
        values = []
        for method in methods:
            rows = [row for row in index.get((dataset, method), []) if _ok(row)]
            measured = {_number(row.get("memory_gib")) for row in rows}
            measured.discard(None)
            if len(measured) > 1:
                notes.append(f"Memory differs across recorded batch sizes or successful rows for {escape(_label(dataset))}/{escape(method)}; its memory cell is unavailable. See [table7.csv](table7.csv) for the individual values.")
                values.append(None)
            else:
                values.append(next(iter(measured), None))
        best = _best(values, minimum=True)
        cells.extend(_cell(value, lambda number: f"{number:.2f}G",
                           value is not None and value == best) for value in values)
    lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.extend(("</tbody>", "</table>"))
    return "\n".join(lines)


def _table7(tables):
    rows = _read_csv(tables / "table7.csv")
    notes = [_common_note("table7.csv"),
             "Insertion and deletion throughputs are operations/second. G denotes GiB; memory is the measured graph-load RSS delta rather than theoretical graph size. The single memory row requires consistent successful measurements across the recorded batch sizes. With at least two available methods, bold marks the highest throughput or lowest memory for each dataset and batch."]
    if (tables / "table7_baselines_notes.md").is_file():
        notes.append("See [baseline measurement notes](table7_baselines_notes.md) for the recorded measurement protocol.")
    index = {}
    for row in rows:
        dataset, method = _dataset(row.get("dataset")), _method(row.get("method"))
        if dataset not in TABLE7_DATASETS:
            notes.append("Measurements for datasets outside LJ, Orkut and Twitter remain available in [table7.csv](table7.csv).")
            continue
        if dataset and method:
            index.setdefault((dataset, method), []).append(row)
        if _integer(row.get("batch_size")) not in BATCHES:
            notes.append("Batch sizes outside 10, 100, 1000, and 10000 remain available in [table7.csv](table7.csv).")
    if not index:
        return _finish("Table 7: Batch updates", ["No measured rows are available."], notes)
    extras = sorted({method for _, method in index} - set(METHODS), key=str.casefold)
    methods = (*METHODS, *extras)
    if extras:
        notes.append("Additional measured methods are appended after the paper's four methods; their original names and values remain in [table7.csv](table7.csv).")
    body = [_batch_table(index, TABLE7_DATASETS, methods, notes)]
    return _finish("Table 7: Batch updates", body, notes)


def format_paper_tables(tables_dir, numbers=(5, 6, 7)):
    """Format selected tables without reading others or writing any files."""
    tables = Path(tables_dir)
    formatters = {5: _table5, 6: _table6, 7: _table7}
    return {f"table{number}": formatters[number](tables) for number in numbers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_run_dir", type=Path, help="saved pipeline run directory containing tables/")
    args = parser.parse_args(argv)
    if __package__:
        from .table_figures import render_table_figures
    else:
        from table_figures import render_table_figures
    report = render_table_figures(args.output_run_dir)
    for name in report["figures"]:
        print(args.output_run_dir / name)
    for issue in report["issues"]:
        print("ERROR: " + str(issue), file=sys.stderr)
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
