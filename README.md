# pdf2docmost

Extract text and tables from PDFs as **Markdown**, ready to paste into
[Docmost](https://docmost.com). Docmost auto-converts pasted Markdown (pipe
tables included) into rich text, so tables stop being a copy-paste chore.

Upload one PDF or many. Two extraction engines, because neither wins alone:

- **Full document** — whole PDF to Markdown in one pass (`pymupdf4llm`), tables inline.
- **Tables only** — each detected table (`pdfplumber`) in an editable grid; fix the
  messy cells, then copy clean Markdown per table or all at once.
- **Plain text** — raw text per page, no table logic.
- **Batch** (multiple files) — convert everything to Markdown and download a ZIP.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the printed URL. On an iPad/phone, browse to it and *Add to Home Screen*.

## Deploy as a Proxmox LXC

`deploy/deploy-pdf2docmost.sh` builds an unprivileged Debian 12 LXC, clones this
repo, sets up a venv, and runs the app as a systemd service. Run it **on the
Proxmox host** as root:

```bash
REPO_URL=https://github.com/KennethDhoe/pdf2docmost.git \
CTID=9000 RAM=1024 STORAGE=local-lvm \
bash deploy/deploy-pdf2docmost.sh
```

It prints `http://<container-ip>:8501` when done.

## Table extraction limits

Merged cells (colspan/rowspan), multi-line cells, and borderless tables are where
extraction breaks. Markdown itself can't express merged cells, so no tool fixes
that — the editable grid in **Tables only** is where you patch them before copying.
Scanned/image PDFs need OCR, which is intentionally not bundled (keeps deps light).

## Security

Streamlit has **no authentication**. The deploy binds `0.0.0.0:8501`. Do not expose
it to the internet — reach it over your LAN, a VPN (e.g. Tailscale), or behind a
reverse proxy that adds auth. Especially relevant if you feed it sensitive documents.
