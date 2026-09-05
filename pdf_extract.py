"""
pdf_extract.py — PDF -> Markdown extraction engine for Docmost.

Pure functions, no UI. Two paths:
  1. full_markdown()   -> whole document as Markdown (fast path, tables inline)
  2. extract_tables()  -> individual tables as DataFrames you can fix before export

Docmost accepts pasted Markdown (pipe tables included) and converts to rich text.
"""

from __future__ import annotations
import io
import re
import pandas as pd
import pymupdf                     # PyMuPDF
import pymupdf4llm
import pdfplumber


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _clean_cell(v) -> str:
    """Make a raw cell safe for a Markdown pipe table."""
    if v is None:
        return ""
    s = str(v).strip()
    s = s.replace("\r", "\n")
    s = re.sub(r"\n+", "<br>", s)      # multi-line cell -> <br>
    s = s.replace("|", r"\|")          # escape pipes
    s = re.sub(r"[ \t]+", " ", s)      # collapse runs of spaces
    return s


def df_to_markdown(df: pd.DataFrame) -> str:
    """DataFrame -> GitHub-style Markdown pipe table (Docmost-friendly)."""
    if df is None or df.empty:
        return ""
    cols = [_clean_cell(c) for c in df.columns]
    # guarantee non-empty, unique-ish headers
    cols = [c if c else f"col{i+1}" for i, c in enumerate(cols)]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = []
    for _, row in df.iterrows():
        rows.append("| " + " | ".join(_clean_cell(x) for x in row.tolist()) + " |")
    return "\n".join([header, sep, *rows])


# ----------------------------------------------------------------------
# path 1: whole document -> Markdown
# ----------------------------------------------------------------------
def full_markdown(pdf_bytes: bytes, pages: list[int] | None = None) -> str:
    """Whole doc (or selected 0-based pages) to Markdown via pymupdf4llm."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return pymupdf4llm.to_markdown(doc, pages=pages, show_progress=False)
    finally:
        doc.close()


# ----------------------------------------------------------------------
# path 2: plain per-page text (no table logic)
# ----------------------------------------------------------------------
def page_texts(pdf_bytes: bytes) -> list[str]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [doc[i].get_text("text") for i in range(doc.page_count)]
    finally:
        doc.close()


# ----------------------------------------------------------------------
# path 3: individual tables -> DataFrames (for hand-fixing)
# ----------------------------------------------------------------------
def _table_settings(strategy: str) -> dict:
    if strategy == "text":            # borderless / whitespace-separated
        return {"vertical_strategy": "text", "horizontal_strategy": "text"}
    return {"vertical_strategy": "lines", "horizontal_strategy": "lines"}  # ruled


def extract_tables(pdf_bytes: bytes, strategy: str = "lines",
                   first_row_header: bool = True) -> list[dict]:
    """
    Returns a list of {page, index, df} for every detected table.
    strategy: "lines" (bordered tables) or "text" (borderless).
    """
    out: list[dict] = []
    settings = _table_settings(strategy)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            try:
                raw_tables = page.extract_tables(table_settings=settings)
            except Exception:
                raw_tables = []
            for tidx, raw in enumerate(raw_tables):
                if not raw or len(raw) < 1:
                    continue
                # drop fully-empty rows
                rows = [r for r in raw if any((c or "").strip() for c in r)]
                if not rows:
                    continue
                if first_row_header and len(rows) > 1:
                    header, body = rows[0], rows[1:]
                    header = [ (h or "").strip() or f"col{i+1}"
                               for i, h in enumerate(header) ]
                    df = pd.DataFrame(body, columns=header)
                else:
                    df = pd.DataFrame(rows)
                df = df.fillna("")
                out.append({"page": pno, "index": tidx + 1, "df": df})
    return out


# ----------------------------------------------------------------------
# smoke test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    data = open(sys.argv[1], "rb").read()
    print("=== FULL MARKDOWN (first 800 chars) ===")
    print(full_markdown(data)[:800])
    print("\n=== TABLES (lines) ===")
    for t in extract_tables(data, "lines"):
        print(f"\n-- page {t['page']} table {t['index']} --")
        print(df_to_markdown(t["df"]))
