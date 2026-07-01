"""Streamlit UI for the Beall's List predatory-venue check.

Launch from the repo root (with the venv active):

    streamlit run ui/app.py

or just run ``run_ui.ps1`` / ``run_ui.sh``.

Paste or upload Semantic Scholar paper records (each with a ``publicationVenue``),
choose the fast (rules-only) or thorough (rules + an LLM pass over the "review"
papers) check, run it, preview the result, and download the Excel.  The UI runs
the same scripts as the command line, so the output is identical.
"""

import io
import json
import os
import re
import sys

import openpyxl
import streamlit as st
from dotenv import load_dotenv

import runners

load_dotenv(runners.REPO_ROOT / ".env")

# In-process validator for the live "Input data quality" panel.
sys.path.insert(0, str(runners.RC))           # src/  (for the `common` package)
sys.path.insert(0, str(runners._BEALLS))      # bealls_list_check/ (the venue checks)
try:
    from common import input_quality           # the shared, field-aware validator
    _VALIDATORS_OK = True
except Exception:
    _VALIDATORS_OK = False

st.set_page_config(page_title="Beall's List check", page_icon="🕵️", layout="wide")
# Best-effort: show Streamlit toasts in the top-right (falls back to default).
st.markdown("<style>div[data-testid='stToast']{position:fixed;top:3.5rem;right:1rem;}</style>",
            unsafe_allow_html=True)


def notify(msg, icon=None):
    """Transient popup; never fatal if the Streamlit build lacks st.toast."""
    try:
        st.toast(msg, icon=icon)
    except Exception:
        pass


_S2_EXAMPLE = json.dumps(
    [{"title": "Example paper", "venue": "Journal of X", "year": 2023,
      "externalIds": {"DOI": "10.1234/x"},
      "publicationVenue": {"name": "Journal of X", "type": "journal",
                           "url": "https://www.scirp.org/journal/x",
                           "issn": "1234-5678", "alternate_urls": []},
      "openAccessPdf": {"url": ""}}], indent=2)


def env_sidebar():
    st.sidebar.header("Environment")
    prov = os.getenv("LLM_PROVIDER", "azure")
    st.sidebar.write(f"**LLM provider:** `{prov}`")
    st.sidebar.write(f"**Model / deployment:** `{os.getenv('AZURE_OPENAI_DEPLOYMENT', '(unset)')}`")
    st.sidebar.caption("The **fast** check needs no keys. The **thorough** check uses an LLM "
                       "(keys read from the repo's `.env`) and costs tokens.")
    with st.sidebar.expander("ℹ️ What this tool does"):
        st.markdown(
            "Checks whether each paper's **journal/publisher is on Beall's List** of "
            "*potentially* predatory venues. A match means *appears on the list* — "
            "**not** that the paper is bad. Input is **Semantic Scholar paper records** "
            "(with a `publicationVenue`), not GitHub links.\n\n"
            "**Fast** = rules only (no LLM). **Thorough** = the fast check plus an LLM "
            "recommendation for each `review` paper.")


def get_papers(key: str):
    """Render the upload/paste widget and return a parsed list of papers, or None."""
    st.caption("Input format: Semantic Scholar **paper records** (with `publicationVenue`). "
               "Upload a JSON **list**, or a **single** paper object.")
    with st.expander("See expected format"):
        st.code(_S2_EXAMPLE, language="json")
    mode = st.radio("Provide papers via", ["Upload file", "Paste JSON"],
                    horizontal=True, key=f"mode_{key}")
    raw = None
    if mode == "Upload file":
        up = st.file_uploader("papers.json", type=["json"], key=f"file_{key}")
        if up is not None:
            raw = up.getvalue().decode("utf-8")
    else:
        raw = st.text_area("Paste JSON here", height=160, key=f"paste_{key}",
                           placeholder=_S2_EXAMPLE)
    if not raw or not raw.strip():
        return None
    try:
        return runners.normalize_upload(json.loads(raw))
    except json.JSONDecodeError as e:
        st.error(f"Invalid JSON: {e}")
        return None
    except ValueError as e:
        st.error(str(e))
        return None


def venue_list_input(label: str, key: str):
    """Optional JSON list of venues for whitelist/blacklist. Returns list or None."""
    raw = st.text_area(label, height=90, key=key,
                       placeholder='[{"name": "IEEE", "domain": "ieee.org"}, "Nature"]')
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        st.error(f"{label}: invalid JSON ({e})")
        return None
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        st.error(f"{label}: must be a JSON list of venues.")
        return None
    return parsed


def show_input_quality(items, key):
    """Up-front panel: validate the Semantic Scholar metadata, offer it as Excel."""
    if not _VALIDATORS_OK:
        return
    try:
        flagged = input_quality.validate_papers(items)   # offline validator
    except Exception as exc:
        st.caption(f"(input validation unavailable: {exc})")
        return
    icon = "⚠️" if flagged else "✅"
    with st.expander(f"{icon} Input data quality (Semantic Scholar venue metadata) — "
                     f"{len(flagged)} of {len(items)} paper(s) flagged", expanded=bool(flagged)):
        st.download_button("⬇️ Download report (.xlsx)",
                           data=runners.build_input_quality_xlsx(items, flagged),
                           file_name="input_data_quality.xlsx", key=f"dqdl_{key}",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if not flagged:
            st.caption("No issues found by the offline checks. (Not a guarantee of correctness; "
                       "deeper checks run during the check itself and show in the run log.)")
        for i, p, flags in flagged[:5]:
            st.markdown(f"**#{i}** {(p.get('title') or '?')[:90]} — " + "; ".join(flags))
        if len(flagged) > 5:
            st.caption(f"…and {len(flagged) - 5} more — download the report above for the full list.")


def _preview(xlsx_bytes: bytes, max_rows: int = 40):
    """Show sheet names + the first sheet's first rows as a table."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception as e:                      # never let a preview crash the page
        st.caption(f"(Preview unavailable: {e})")
        return
    st.caption("Sheets: " + ", ".join(wb.sheetnames))
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return
    headers = [str(h) if h is not None else f"col{i}" for i, h in enumerate(rows[0])]
    data = [dict(zip(headers, [("" if v is None else v) for v in r])) for r in rows[1:max_rows + 1]]
    st.dataframe(data, use_container_width=True)
    if len(rows) > max_rows + 1:
        st.caption(f"(showing first {max_rows} of {len(rows) - 1} rows — download for the full sheet)")


def render_result(res: runners.RunResult, checker: runners.Checker):
    if res.ok:
        st.success(f'✅ Done — "{checker.label}" finished. Your Excel is ready below.')
        st.download_button(f"⬇️ Download {res.output_path.name}", data=res.output_bytes,
                           file_name=res.output_path.name, key=f"dl_{checker.id}",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        _preview(res.output_bytes)
    else:
        st.error(f"The checker exited with code {res.returncode} and produced no output file. "
                 "See the run log below.")
    with st.expander("Run log (incl. the input data-quality report)", expanded=not res.ok):
        if res.stdout:
            st.text(res.stdout[-8000:])
        if res.stderr:
            st.caption("stderr")
            st.text(res.stderr[-4000:])


def _checker_badge(checker):
    if checker.needs_llm:
        return "🔑 Uses an LLM — needs API keys in `.env`, and costs tokens."
    return "⚡ No LLM — matched offline against the vendored Beall's List snapshot."


# ── Page ────────────────────────────────────────────────────────────────────
st.title("🕵️ Beall's List predatory-venue check")
st.caption("Is the paper's **journal/publisher on Beall's List** of potentially predatory "
           "venues? Add your papers as JSON, run, and download the Excel report.")
env_sidebar()

# Step 1 — choose a check
st.markdown("**Step 1 · Choose a check**")
labels = [c.label for c in runners.CHECKERS]
label = st.selectbox("Check", labels, key="sel", label_visibility="collapsed")
checker = runners.CHECKERS[labels.index(label)]
st.markdown(f"➡️ {checker.description}")
st.caption(_checker_badge(checker))

# Step 2 — provide the papers
st.markdown("**Step 2 · Provide the papers**")
items = get_papers(checker.id)
if items is not None:
    st.success(f"✅ Loaded {len(items)} paper(s) — ready to run.")
    show_input_quality(items, key=checker.id)

# Step 3 — options
with st.expander("⚙️ Options (optional)"):
    st.markdown("**Scope — whitelist** (optional)")
    wl = venue_list_input("Only check papers from these venues — leave empty to check all",
                          f"wl_{checker.id}")
    st.markdown("**Extend the list — blacklist** (optional)")
    bl = venue_list_input("Also treat papers from these venues as predatory",
                          f"bl_{checker.id}")
    st.caption('Format for both boxes: a JSON list like '
               '`[{"name": "IEEE", "domain": "ieee.org"}]` (a plain `"name"` string works too).')
    crossref = st.checkbox(
        "Cross-check each venue against Crossref (recommended; skipped automatically when offline)",
        value=True,
        key=f"cr_{checker.id}",
        help="Crossref is the official registry of published articles (looked up by each "
             "paper's DOI). When on (the default), the journal name and ISSN that Semantic "
             "Scholar reports are compared against Crossref's official record, and DOIs are "
             "used to resolve uncertain matches — catching papers where Semantic Scholar has "
             "the wrong venue. It contacts Crossref once per paper (a little slower) and is "
             "skipped automatically when there's no internet. Uncheck to force a fully offline run.")
aux_lists = {"--whitelist": wl, "--blacklist": bl}
extra_args = None if crossref else ["--crossref", "off"]

# Step 3 — run
st.markdown("**Step 3 · Run**")
run = st.button("▶ Run check", type="primary", key="run",
                disabled=items is None,
                help=None if items is not None else "Add papers in Step 2 first.")
if not items:
    st.caption("Add papers above to enable the button.")
if run and items is not None:
    progress = st.progress(0.0, text="Starting…")
    log_line = st.empty()
    step = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]")   # the LLM pass prints "[i/N]"
    pulse = {"v": 0.0}

    def on_line(line):
        m = step.search(line)
        if m and int(m.group(2)):
            progress.progress(min(int(m.group(1)) / int(m.group(2)), 1.0),
                              text=f"{m.group(1)}/{m.group(2)} venues…")
        else:                                    # no per-item counter — keep the bar alive
            pulse["v"] = (pulse["v"] + 0.05) % 0.9
            progress.progress(pulse["v"], text="Working…")
        if line.strip():
            log_line.caption("⏳ " + line[-140:])

    try:
        with st.spinner(f'Running "{checker.label}"… '
                        "large corpora and the LLM pass can take a while."):
            res = runners.run_checker(checker, items, aux_lists=aux_lists,
                                      extra_args=extra_args, on_line=on_line)
    except Exception as e:
        progress.empty(); log_line.empty()
        st.session_state["res"] = None
        st.error("Could not start the check:")
        st.exception(e)
        st.stop()
    progress.progress(1.0, text="Done")
    log_line.empty()
    st.session_state["res"] = res
    notify("Check complete ✅" if res.ok else "Check failed 🛑",
           icon="✅" if res.ok else "🛑")

res = st.session_state.get("res")
if res is not None:
    render_result(res, checker)
