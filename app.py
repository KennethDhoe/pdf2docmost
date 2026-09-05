"""
PDF -> Docmost Markdown extractor.  Run:  streamlit run app.py

Multi-file: upload one or many PDFs.
  - >1 file  -> a batch panel converts all to Markdown and hands back a ZIP,
                plus a picker to inspect any single file in detail.
  - each file -> Full document / Tables only / Plain text tabs.

Docmost auto-converts pasted Markdown (pipe tables included) to rich text.
"""

import io
import zipfile

import streamlit as st
import pandas as pd

import pdf_extract as px

st.set_page_config(page_title="PDF → Docmost", layout="wide")
st.title("PDF → Docmost Markdown")
st.caption("Extract text and tables as Markdown, ready to paste into Docmost.")

uploaded = st.file_uploader(
    "Drop one or more PDFs", type=["pdf"], accept_multiple_files=True
)

if not uploaded:
    st.info("Upload at least one PDF to start.")
    st.stop()

names = [f.name for f in uploaded]
by_name = {f.name: f for f in uploaded}


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0]


# ----------------------------------------------------------------------
# Batch panel (only when more than one file)
# ----------------------------------------------------------------------
if len(uploaded) > 1:
    with st.expander(f"📦 Batch — convert all {len(uploaded)} files to Markdown", expanded=True):
        st.markdown("One ZIP with a `.md` per PDF (full-document conversion).")
        if st.button("Convert all → ZIP"):
            buf = io.BytesIO()
            with st.spinner("Converting all files…"):
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in uploaded:
                        try:
                            md = px.full_markdown(f.getvalue())
                        except Exception as e:  # keep going on a bad file
                            md = f"<!-- extraction failed: {e} -->"
                        zf.writestr(_stem(f.name) + ".md", md)
            buf.seek(0)
            st.download_button(
                "⬇ Download all_markdown.zip", buf,
                file_name="all_markdown.zip", mime="application/zip",
            )
    pick = st.selectbox("Inspect a single file", names)
else:
    pick = names[0]

active = by_name[pick]
pdf_bytes = active.getvalue()
stem = _stem(active.name)

st.divider()
st.markdown(f"**Inspecting:** `{active.name}`")

full_tab, tables_tab, text_tab = st.tabs(
    ["📄 Full document", "🧮 Tables only", "🔤 Plain text (per page)"]
)

# ----------------------------------------------------------------------
with full_tab:
    st.markdown(
        "Whole document to Markdown. Fastest path — grab the block, paste into "
        "Docmost. Check any table that had merged/multi-line cells."
    )
    with st.spinner("Converting…"):
        md = px.full_markdown(pdf_bytes)
    st.download_button("⬇ Download .md", md, file_name=stem + ".md",
                       mime="text/markdown")
    st.code(md, language="markdown")
    with st.expander("Rendered preview"):
        st.markdown(md)

# ----------------------------------------------------------------------
with tables_tab:
    c1, c2 = st.columns([1, 1])
    with c1:
        strategy = st.selectbox(
            "Detection strategy",
            ["lines", "text"],
            format_func=lambda s: {
                "lines": "lines — bordered / ruled tables",
                "text": "text — borderless (whitespace-aligned)",
            }[s],
            help="Start with 'lines'. If a borderless table comes out empty or "
                 "garbled, switch to 'text'.",
            key=f"strat_{pick}",
        )
    with c2:
        first_row_header = st.toggle("First row is header", value=True,
                                     key=f"hdr_{pick}")

    with st.spinner("Detecting tables…"):
        tables = px.extract_tables(pdf_bytes, strategy, first_row_header)

    if not tables:
        st.warning(
            "No tables detected with this strategy. Try the other strategy, or "
            "use the Full document tab."
        )
    else:
        st.success(f"{len(tables)} table(s) detected. Edit any cell, then copy.")
        all_md = []
        for t in tables:
            label = f"Page {t['page']} · table {t['index']}"
            st.subheader(label)
            edited = st.data_editor(
                t["df"], key=f"ed_{pick}_{t['page']}_{t['index']}",
                num_rows="dynamic", use_container_width=True,
            )
            md_tbl = px.df_to_markdown(edited)
            all_md.append(f"<!-- {label} -->\n{md_tbl}")
            st.code(md_tbl, language="markdown")
            st.divider()

        st.subheader("All tables")
        st.download_button("⬇ Download all tables .md", "\n\n".join(all_md),
                           file_name=stem + "_tables.md", mime="text/markdown")
        st.code("\n\n".join(all_md), language="markdown")

# ----------------------------------------------------------------------
with text_tab:
    st.markdown("Raw text per page — no table structure.")
    for i, txt in enumerate(px.page_texts(pdf_bytes), start=1):
        with st.expander(f"Page {i}"):
            st.code(txt, language="text")
