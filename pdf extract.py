"""
pdf_extract.py — PDF -> Markdown extraction engine for Docmost.

Pure functions, no UI.
  full_markdown()  -> whole document (or given pages) as Markdown, tables inline
  page_texts()     -> plain text per page
  extract_tables() -> tables as cleaned DataFrames (merged cells recovered)
  df_to_markdown() -> DataFrame to a Docmost-friendly pipe table

Table extraction uses PyMuPDF's detector, then a cleanup pass that undoes the
merged-cell damage typical of real-world PDFs (values sprayed across phantom
columns, empty spacer columns, banner rows). See _clean_grid.
"""

from __future__ import annotations
import re
import pandas as pd
import pymupdf
import pymupdf4llm


# ----------------------------------------------------------------------
# markdown / text
# ----------------------------------------------------------------------
def full_markdown(pdf_bytes: bytes, pages: list[int] | None = None) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return pymupdf4llm.to_markdown(doc, pages=pages, show_progress=False)
    finally:
        doc.close()


def page_texts(pdf_bytes: bytes) -> list[str]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [doc[i].get_text("text") for i in range(doc.page_count)]
    finally:
        doc.close()


# ----------------------------------------------------------------------
# table cleanup
# ----------------------------------------------------------------------
_COLPH = re.compile(r"^Col\d+$")


def _norm(x) -> str:
    if x is None:
        return ""
    s = re.sub(r"\s+", " ", str(x).replace("**", "").strip())
    return "" if _COLPH.match(s) else s


def _clean_grid(rows: list[list]) -> list[list[str]]:
    """
    Undo merged-cell artifacts:
      1. normalise cells, pad ragged rows, drop empty rows
      2. drop fully-empty columns (spacer columns) FIRST
      3. merge adjacent columns that are duplicates/complements (a merged cell
         whose value was sprayed across several grid columns), using only
         'informative' rows (>=2 distinct values) so banner/title rows don't
         collapse the whole table
      4. drop empty columns again, drop rows identical to the header
    """
    rows = [[_norm(c) for c in r] for r in rows]
    if not rows:
        return []
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []

    # 2) drop fully-empty columns first (this is what prevents an empty spacer
    #    column from bridging two real columns during the merge step)
    ncol = len(rows[0])
    keep = [j for j in range(ncol) if any(r[j] for r in rows)]
    rows = [[r[j] for j in keep] for r in rows]
    ncol = len(keep)
    if ncol == 0:
        return []

    # 3) merge adjacent duplicate/complementary columns
    info = [r for r in rows if len({c for c in r if c}) >= 2]
    groups = [[0]]
    for j in range(1, ncol):
        merge = bool(info)
        for r in info:
            for jj in groups[-1]:
                a, b = r[jj], r[j]
                if a and b and a != b:
                    merge = False
                    break
            if not merge:
                break
        groups[-1].append(j) if merge else groups.append([j])

    def pick(r, g, header=False):
        if header:
            for j in g:
                if r[j] and not _COLPH.match(r[j]):
                    return r[j]
        for j in g:
            if r[j]:
                return r[j]
        return ""

    out = [[pick(rows[0], g, header=True) for g in groups]]
    out += [[pick(r, g) for g in groups] for r in rows[1:]]

    # 4) final empty-column drop + drop rows equal to the header
    keep = [j for j in range(len(out[0])) if any(row[j] for row in out)]
    out = [[row[j] for j in keep] for row in out]
    if not out:
        return []
    hdr = out[0]
    body = [r for r in out[1:] if r != hdr]
    return [hdr] + body


def _unique_headers(row: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, h in enumerate(row):
        h = h if (h and not _COLPH.match(h)) else f"col{i+1}"
        n = seen.get(h, 0)
        seen[h] = n + 1
        out.append(h if n == 0 else f"{h} ({n+1})")
    return out


# ----------------------------------------------------------------------
# table extraction
# ----------------------------------------------------------------------

def _parse_md_table(md: str) -> list[list[str]]:
    rows = []
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if cells and all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells):
            continue  # skip |---|---| separator
        rows.append(cells)
    return rows

def extract_tables(pdf_bytes: bytes, strategy: str = "lines",
                   first_row_header: bool = True) -> list[dict]:
    """
    Detect tables with PyMuPDF, clean merged-cell damage, return
    [{page, index, df}] with 1-based page numbers.
    strategy: "lines" (bordered/ruled) or "text" (borderless).
    """
    out: list[dict] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for pno in range(doc.page_count):
            try:
                finder = doc[pno].find_tables(strategy=strategy)
            except Exception:
                continue
            for tidx, tbl in enumerate(finder.tables):
                try:
                    grid = _clean_grid(_parse_md_table(tbl.to_markdown()))
                except Exception:
                    continue
                # skip fragments that aren't really tables
                if len(grid) < 2 or len(grid[0]) < 2:
                    continue
                if first_row_header:
                    df = pd.DataFrame(grid[1:], columns=_unique_headers(grid[0]))
                else:
                    df = pd.DataFrame(grid)
                out.append({"page": pno + 1, "index": tidx + 1, "df": df})
    finally:
        doc.close()
    return out


# ----------------------------------------------------------------------
# DataFrame -> Markdown pipe table
# ----------------------------------------------------------------------
def _cell(v) -> str:
    if v is None:
        return ""
    s = re.sub(r"\s+", " ", str(v).strip())
    return s.replace("|", r"\|")


def df_to_markdown(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return ""
    cols = [_cell(c) or f"col{i+1}" for i, c in enumerate(df.columns)]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = ["| " + " | ".join(_cell(x) for x in row.tolist()) + " |"
            for _, row in df.iterrows()]
    return "\n".join([header, sep, *rows])
