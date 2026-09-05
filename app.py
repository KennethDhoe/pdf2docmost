"""
PDF -> Docmost Markdown extractor.  Run:  streamlit run app.py

Built for large PDFs. Instead of converting the whole document (slow, and the
output can be too big to render), you work one page-range at a time:

  - If the PDF has real bookmarks (a table of contents), you browse by chapter.
  - If it doesn't (many PDFs, including scanned/exported ones), you split into
    fixed page ranges and convert range by range.

Each range is sliced into a small sub-PDF and converted on its own, so both the
conversion and the browser render stay fast. Conversion, tables, and plain text
are all scoped to the selected range.

Docmost auto-converts pasted Markdown (pipe tables included) to rich text.
"""

import io
import re
import zipfile

import streamlit as st
import pandas as pd
import pymupdf

import pdf_extract as px

VERSION = "range-scoped v4"   # if you don't see this in the app, you're on old code

st.set_page_config(page_title="PDF → Docmost", layout="wide")
st.title("PDF → Docmost Markdown")
st.caption(f"Convert a PDF one page-range at a time · build: {VERSION}")

# Above this many characters we don't auto-render inline (browser can hang).
RENDER_LIMIT = 50_000
# A TOC needs at least this many real entries before we offer chapter browsing.
MIN_TOC = 3
_JUNK_TITLES = {"", "blank page", "blank", "cover"}


# ----------------------------------------------------------------------
# cached engine calls (keyed on the small sliced sub-PDF, not the big file)
# ----------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_outline(pdf_bytes: bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return doc.get_toc(simple=True), doc.page_count
    finally:
        doc.close()


@st.cache_data(show_spinner=False)
def slice_pdf(pdf_bytes: bytes, a0: int, b0: int) -> bytes:
    """Sub-PDF of 0-based pages a0..b0 inclusive."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    nd = pymupdf.open()
    nd.insert_pdf(doc, from_page=a0, to_page=b0)
    out = nd.tobytes()
    nd.close()
    doc.close()
    return out


@st.cache_data(show_spinner=False)
def convert_bytes(sub_bytes: bytes) -> str:
    return px.full_markdown(sub_bytes)


@st.cache_data(show_spinner=False)
def tables_bytes(sub_bytes: bytes, strategy: str, header: bool):
    return px.extract_tables(sub_bytes, strategy, header)


@st.cache_data(show_spinner=False)
def texts_bytes(sub_bytes: bytes):
    return px.page_texts(sub_bytes)


# ----------------------------------------------------------------------
# chapter / range builders
# ----------------------------------------------------------------------
def usable_toc(toc):
    out = []
    for lvl, title, pg in toc:
        t = (title or "").strip()
        if pg and pg >= 1 and t and t.lower() not in _JUNK_TITLES:
            out.append((lvl, t, pg))
    return out


def build_chapters(toc_entries, page_count, depth):
    pts = [(l, t, p) for (l, t, p) in toc_entries if l <= depth]
    if not pts:
        return []
    chapters = []
    if pts[0][2] > 1:
        chapters.append({"title": "Front matter", "start": 1, "end": pts[0][2] - 1})
    for i, (l, t, p) in enumerate(pts):
        end = pts[i + 1][2] - 1 if i + 1 < len(pts) else page_count
        chapters.append({"title": t, "start": p, "end": max(p, end)})
    return chapters


def chunk_chapters(page_count, size):
    out = []
    for a in range(1, page_count + 1, size):
        b = min(a + size - 1, page_count)
        out.append({"title": f"Pages {a}–{b}", "start": a, "end": b})
    return out


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0]


# ----------------------------------------------------------------------
# input
# ----------------------------------------------------------------------
uploaded = st.file_uploader(
    "Drop one or more PDFs", type=["pdf"], accept_multiple_files=True
)
if not uploaded:
    st.info("Upload at least one PDF to start.")
    st.stop()

names = [f.name for f in uploaded]
by_name = {f.name: f for f in uploaded}
pick = st.selectbox("File", names) if len(uploaded) > 1 else names[0]

active = by_name[pick]
pdf_bytes = active.getvalue()
stem = _stem(active.name)

toc_raw, page_count = get_outline(pdf_bytes)
toc = usable_toc(toc_raw)
have_toc = len(toc) >= MIN_TOC

st.caption(f"`{active.name}` · {page_count} page(s) · "
           + (f"{len(toc)} bookmark(s)" if have_toc else "no usable bookmarks"))

# ----------------------------------------------------------------------
# how to split
# ----------------------------------------------------------------------
if have_toc:
    max_lvl = min(3, max(l for l, _, _ in toc))
    source = st.radio("Split by", ["Bookmarks", "Fixed page ranges"],
                      horizontal=True, key=f"src_{pick}")
else:
    source = "Fixed page ranges"
    st.info("This PDF has no usable bookmarks, so there are no chapter names to "
            "browse. Split it into fixed page ranges instead — pick a size below.")

if source == "Bookmarks":
    depth = (st.slider("Split on heading level", 1, max_lvl, 1, key=f"depth_{pick}")
             if max_lvl > 1 else 1)
    chapters = build_chapters(toc, page_count, depth)
else:
    size = st.number_input("Pages per range", 1, 200, 10, key=f"chunk_{pick}",
                           help="Smaller = faster per piece and safer to render. "
                                "10 keeps each piece well under the render limit.")
    chapters = chunk_chapters(page_count, int(size))

if not chapters:
    st.warning("Could not derive ranges. Switch to fixed page ranges.")
    st.stop()

labels = [f"{i+1}. {c['title']}  (p{c['start']}–{c['end']})"
          for i, c in enumerate(chapters)]
ci = st.selectbox(f"Piece ({len(chapters)} total)", range(len(chapters)),
                  format_func=lambda i: labels[i], key=f"chap_{pick}")
chap = chapters[ci]
a0, b0 = chap["start"] - 1, chap["end"] - 1

# slice once; every tab works off this small sub-PDF
sub = slice_pdf(pdf_bytes, a0, b0)
n_pages = chap["end"] - chap["start"] + 1

st.divider()
st.markdown(f"### {chap['title']}  ·  pages {chap['start']}–{chap['end']}")
if n_pages >= page_count:
    st.error(f"This range covers all {page_count} pages — that IS the whole "
             "document. Switch to 'Fixed page ranges' and set a small size.")
else:
    st.info(f"Scoped to {n_pages} page(s) ({chap['start']}–{chap['end']}) of "
            f"{page_count}. The rest of the document is not touched.")

md_tab, tbl_tab, txt_tab = st.tabs(
    ["📄 → Markdown", "🧮 Tables", "🔤 Plain text"]
)

# ----------------------------------------------------------------------
# range -> markdown
# ----------------------------------------------------------------------
with md_tab:
    conv_key = f"conv_{pick}_{ci}"
    if st.button(f"▶ Convert pages {chap['start']}–{chap['end']}  ({n_pages} page(s))",
                 key=f"btn_{pick}_{ci}", type="primary"):
        with st.spinner(f"Converting pages {chap['start']}–{chap['end']} only…"):
            st.session_state[conv_key] = convert_bytes(sub)

    md = st.session_state.get(conv_key)
    if md is None:
        st.caption("Nothing converted yet. Click the button to convert just "
                   "this range.")
    else:
        st.download_button("⬇ Download .md", md,
                           file_name=f"{stem}_{ci+1:03d}.md", mime="text/markdown",
                           key=f"dl_{pick}_{ci}")
        st.caption(f"{len(md):,} characters from pages "
                   f"{chap['start']}–{chap['end']}")
        if len(md) > RENDER_LIMIT:
            st.warning("This range is large — rendering inline may hang the "
                       "browser. Use the download, or expand below. Tip: use a "
                       "smaller page-range size.")
            with st.expander("Show Markdown inline anyway"):
                with st.container(height=500):
                    st.code(md, language="markdown")
        else:
            with st.container(height=500):
                st.code(md, language="markdown")
            with st.expander("Rendered preview"):
                with st.container(height=500):
                    st.markdown(md)

    with st.expander("Convert EVERYTHING → ZIP (slow on big PDFs)"):
        st.caption(f"Converts all {len(chapters)} pieces and bundles the .md files. "
                   f"On a large PDF this can take a few minutes.")
        if st.button("Build ZIP", key=f"zip_{pick}"):
            buf = io.BytesIO()
            prog = st.progress(0.0)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for j, c in enumerate(chapters):
                    s = slice_pdf(pdf_bytes, c["start"] - 1, c["end"] - 1)
                    zf.writestr(f"{stem}_{j+1:03d}.md", convert_bytes(s))
                    prog.progress((j + 1) / len(chapters))
            buf.seek(0)
            st.download_button("⬇ Download all.zip", buf,
                               file_name=f"{stem}_all.zip",
                               mime="application/zip", key=f"zdl_{pick}")

# ----------------------------------------------------------------------
# tables in this range
# ----------------------------------------------------------------------
with tbl_tab:
    c1, c2 = st.columns(2)
    strategy = c1.selectbox(
        "Detection strategy", ["lines", "text"],
        format_func=lambda s: {"lines": "lines — bordered / ruled",
                               "text": "text — borderless"}[s],
        key=f"strat_{pick}",
    )
    header = c2.toggle("First row is header", value=True, key=f"hdr_{pick}")

    with st.spinner("Detecting tables…"):
        tables = tables_bytes(sub, strategy, header)

    if not tables:
        st.warning("No tables detected here with this strategy. Try the other one.")
    else:
        st.success(f"{len(tables)} table(s). Edit any cell, then copy.")
        all_md = []
        for t in tables:
            orig_page = chap["start"] + t["page"] - 1   # map slice page -> original
            label = f"Page {orig_page} · table {t['index']}"
            st.subheader(label)
            edited = st.data_editor(
                t["df"], key=f"ed_{pick}_{ci}_{t['page']}_{t['index']}",
                num_rows="dynamic", use_container_width=True,
            )
            mdt = px.df_to_markdown(edited)
            all_md.append(f"<!-- {label} -->\n{mdt}")
            st.code(mdt, language="markdown")
            st.divider()
        st.download_button("⬇ Download these tables .md", "\n\n".join(all_md),
                           file_name=f"{stem}_{ci+1:03d}_tables.md",
                           mime="text/markdown", key=f"tdl_{pick}_{ci}")

# ----------------------------------------------------------------------
# plain text (range pages only)
# ----------------------------------------------------------------------
with txt_tab:
    texts = texts_bytes(sub)
    for offset, t in enumerate(texts):
        orig_page = chap["start"] + offset
        with st.expander(f"Page {orig_page}"):
            st.code(t, language="text")
