"""Export measured CSV tables as paper-style, table-only PDF figures.

Measurement CSVs and the original experimental plotting scripts are never
changed by this exporter. Formatting is generated in memory from the CSVs.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile

if __package__:
    from .paper_tables import format_paper_tables
else:
    from paper_tables import format_paper_tables


SUPERSCRIPTS = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")


@dataclass
class Cell:
    text: str
    row: int
    column: int
    rowspan: int = 1
    colspan: int = 1
    bold: bool = False
    header: bool = False


@dataclass
class Table:
    cells: list[Cell]
    rows: int
    columns: int
    header_rows: int


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.rows = None
        self.row = None
        self.cell = None
        self.sup = None
        self.in_head = False
        self.header_rows = 0

    @staticmethod
    def _span(attributes, name):
        try:
            value = int(attributes.get(name, "1"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid {name}") from error
        if not 1 <= value <= 1000:
            raise ValueError(f"Invalid {name}: {value}")
        return value

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.rows is not None:
                raise ValueError("Nested tables are unsupported")
            self.rows, self.header_rows = [], 0
        elif self.rows is None:
            return
        elif tag == "thead":
            self.in_head = True
        elif tag == "tr":
            if self.row is not None:
                raise ValueError("Unclosed table row")
            self.row = []
            if self.in_head:
                self.header_rows += 1
        elif tag in ("th", "td"):
            if self.row is None or self.cell is not None:
                raise ValueError("Table cell outside a row or inside another cell")
            attributes = dict(attrs)
            self.cell = {"parts": [], "rowspan": self._span(attributes, "rowspan"),
                         "colspan": self._span(attributes, "colspan"),
                         "header": tag == "th", "bold": False}
        elif self.cell is not None:
            if tag in ("strong", "b"):
                self.cell["bold"] = True
            elif tag == "br":
                self.cell["parts"].append("\n")
            elif tag == "sup":
                self.sup = []

    def handle_endtag(self, tag):
        if self.rows is None:
            return
        if tag == "sup" and self.sup is not None:
            self.cell["parts"].append("".join(self.sup).translate(SUPERSCRIPTS))
            self.sup = None
        elif tag in ("th", "td"):
            if self.cell is None or self.sup is not None:
                raise ValueError("Unclosed or unmatched table cell")
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr":
            if self.row is None or self.cell is not None:
                raise ValueError("Unclosed or unmatched table row")
            self.rows.append(self.row)
            self.row = None
        elif tag == "thead":
            self.in_head = False
        elif tag == "table":
            if self.row is not None or self.cell is not None:
                raise ValueError("Unclosed table row or cell")
            if self.rows:
                self.tables.append(self._place_cells())
            self.rows = None
            self.in_head = False

    def handle_data(self, data):
        if self.cell is not None:
            (self.sup if self.sup is not None else self.cell["parts"]).append(data)

    def _place_cells(self):
        cells, occupied, columns = [], set(), 0
        for row_number, row in enumerate(self.rows):
            column = 0
            for item in row:
                while (row_number, column) in occupied:
                    column += 1
                end_row = row_number + item["rowspan"]
                end_column = column + item["colspan"]
                positions = {(r, c) for r in range(row_number, end_row)
                             for c in range(column, end_column)}
                if end_row > len(self.rows) or positions & occupied:
                    raise ValueError("Overlapping cells or a rowspan beyond the table")
                occupied.update(positions)
                cells.append(Cell("".join(item["parts"]).strip(), row_number, column,
                                  item["rowspan"], item["colspan"], item["bold"], item["header"]))
                column = end_column
                columns = max(columns, column)
        if not columns or len(occupied) != len(self.rows) * columns:
            raise ValueError("Incomplete rectangular table")
        return Table(cells, len(self.rows), columns, self.header_rows)


def parse_html_tables(markdown):
    """Read table cells and place merged cells on a rectangular grid."""
    parser = _TableParser()
    parser.feed(markdown)
    parser.close()
    if parser.rows is not None:
        raise ValueError("Unclosed HTML table")
    return parser.tables


def _owned_files(figures, name):
    return [figures / f"{name}.pdf", figures / f"{name}.png",
            *figures.glob(f"{name}_additional*.png")]


def _remove_owned(figures, name, keep=()):
    for path in _owned_files(figures, name):
        if path not in keep:
            path.unlink(missing_ok=True)


def _draw_table(model):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import Rectangle
    from matplotlib.textpath import TextToPath

    font_size, padding = 10.5, 14
    text_path = TextToPath()

    def text_width(text, bold=False):
        font = FontProperties(family="DejaVu Serif", size=font_size,
                              weight="bold" if bold else "normal")
        return max((text_path.get_text_width_height_descent(line, font, False)[0]
                    for line in text.splitlines()), default=0)

    widths = [42.0] * model.columns
    heights = [26.0] * model.rows
    # Narrow cells establish base widths before the merged headings are fitted.
    for cell in sorted(model.cells, key=lambda item: item.colspan):
        span = slice(cell.column, cell.column + cell.colspan)
        deficit = text_width(cell.text, bold=cell.bold or cell.header) + padding - sum(widths[span])
        if deficit > 0:
            widths[span] = [width + deficit / cell.colspan for width in widths[span]]
        vertical = max(1, len(cell.text.splitlines())) * font_size * 1.3 + 11
        span = slice(cell.row, cell.row + cell.rowspan)
        deficit = vertical - sum(heights[span])
        if deficit > 0:
            heights[span] = [height + deficit / cell.rowspan for height in heights[span]]

    margin = 8.0
    grid_width, grid_height = sum(widths), sum(heights)
    width, height = grid_width + margin * 2, grid_height + margin * 2
    figure = Figure(figsize=(width / 72, height / 72), facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set(xlim=(0, width), ylim=(0, height))
    axes.set_axis_off()
    left, top = margin, height - margin
    x = [left]
    for value in widths:
        x.append(x[-1] + value)
    y = [top]
    for value in heights:
        y.append(y[-1] - value)
    for cell in model.cells:
        x0, x1 = x[cell.column], x[cell.column + cell.colspan]
        y0, y1 = y[cell.row + cell.rowspan], y[cell.row]
        axes.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0,
                                 facecolor="white", edgecolor="#333333", linewidth=0.45))
        axes.text((x0 + x1) / 2, (y0 + y1) / 2, cell.text,
                  fontsize=font_size, family="DejaVu Serif",
                  weight="bold" if cell.bold or cell.header else "normal",
                  ha="center", va="center", linespacing=1.3, parse_math=False)
    if model.header_rows:
        axes.plot((left, left + grid_width), (y[model.header_rows],) * 2,
                  color="#222222", linewidth=0.9)
    return figure


def _render_pages(figures, name, models):
    import matplotlib
    from matplotlib.backends.backend_pdf import PdfPages

    completed, temporary, pages = [], [], []

    def temporary_path(suffix):
        descriptor, path = tempfile.mkstemp(prefix=f".{name}-", suffix=suffix, dir=figures)
        os.close(descriptor)
        temporary.append(Path(path))
        return Path(path)

    try:
        with matplotlib.rc_context({"pdf.fonttype": 42, "ps.fonttype": 42,
                                    "font.family": "DejaVu Serif", "text.usetex": False}):
            for model in models:
                pages.append(_draw_table(model))
            pdf = figures / f"{name}.pdf"
            pdf_temp = temporary_path(".pdf")
            with PdfPages(pdf_temp) as document:
                for page in pages:
                    document.savefig(page, facecolor="white")
            os.replace(pdf_temp, pdf)
            completed.append(pdf)
        _remove_owned(figures, name, keep=completed)
        return completed
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
        for page in pages:
            page.clear()


def render_table_figures(output_run_dir):
    """Render CSV measurements and remove known obsolete Markdown exports."""
    output = Path(output_run_dir)
    figures = output / "figures"
    report = {"figures": [], "tables": [], "issues": []}
    for number in (5, 6, 7):
        name = f"table{number}"
        record = {"artifact": name, "status": "skipped",
                  "source": f"tables/{name}.csv", "figures": []}
        report["tables"].append(record)
        try:
            (output / "tables" / f"{name}.md").unlink(missing_ok=True)
            formatted = format_paper_tables(output / "tables", numbers=(number,))
            models = parse_html_tables(formatted.get(name, ""))
            if not models:
                _remove_owned(figures, name)
                record["reason"] = "No formatted result table is available."
                continue
            figures.mkdir(parents=True, exist_ok=True)
            paths = _render_pages(figures, name, models)
            record.update(status="ok", pages=len(models),
                          figures=[str(path.relative_to(output)) for path in paths])
            report["figures"].extend(record["figures"])
        except Exception as error:
            _remove_owned(figures, name)
            record.update(status="failed", reason=str(error))
            report["issues"].append({"artifact": name, "status": "failed",
                                     "source": record["source"], "detail": str(error)})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_run_dir", type=Path, help="saved run containing tables/table5.csv–table7.csv")
    args = parser.parse_args(argv)
    report = render_table_figures(args.output_run_dir)
    print(json.dumps(report, indent=2))
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
