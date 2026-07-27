"""Streamlit UI for the Beall's List predatory-venue check.

Launch from the repo root (with the venv active):

    streamlit run ui/app.py

or just run ``run_ui.ps1`` / ``run_ui.sh``.

Paste or upload Semantic Scholar paper records (each with a ``publicationVenue``),
choose the fast (rules-only) or thorough (rules + an LLM pass over the "review"
papers) check, run it, preview the result, and download the Excel.  The UI runs
the same scripts as the command line, so the output is identical.

The app has two pages, listed in the sidebar: **Run a check** (the four-step flow
below) and **Tutorial**, which renders ``ui/TUTORIAL.md`` — the same walkthrough
that is readable on GitHub, so the instructions exist in exactly one place.
"""

import io
import json
import os
import re
import sys
from pathlib import Path

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

# The corpus shipped for verifying an installation (see examples/README.md),
# offered in Step 2 so the app can be tried without supplying data.
_EXAMPLE_CORPUS = runners.REPO_ROOT / "examples" / "sample_papers.json"

_UI_DIR = Path(__file__).resolve().parent
_TUTORIAL_MD = _UI_DIR / "TUTORIAL.md"          # rendered by the Tutorial page


@st.cache_data(show_spinner=False)
def llm_status():
    """Whether the configured LLM provider is usable: ``(ok, message, model)``.

    Answered by the same ``validate_provider_config()`` the checks call, so the
    thorough check cannot be reported as ready and then fail on the first
    request.  ``ok is None`` means the question could not be answered — the
    caller stays quiet rather than guessing.  *model* is the deployment the
    check will actually use, which is the **first** name when several are
    configured.

    Deliberately not called on every page load: it imports litellm, which costs
    a few seconds, and only the thorough check needs the answer.
    """
    try:
        import openai_client
    except Exception as exc:
        return None, str(exc), ""
    try:
        openai_client.validate_provider_config()
        return True, "", openai_client.AZURE_OPENAI_DEPLOYMENT
    except Exception as exc:
        return False, str(exc), openai_client.AZURE_OPENAI_DEPLOYMENT


def sidebar():
    """Sidebar content, below the page links: orientation first, settings last.

    What the tool does is the one thing every visitor needs, so it is stated
    outright.  The LLM configuration is a detail of a single check — and is
    reported again, in context, when that check is selected — so it is demoted to
    a collapsed expander rather than given the sidebar's headline.
    """
    st.sidebar.markdown("**What this tool does**")
    st.sidebar.caption(
        "Checks whether each paper's **journal or publisher is on Beall's List** of "
        "*potentially* predatory venues. A match means *appears on the list* — "
        "**not** that the paper is bad. Input is **Semantic Scholar paper records** "
        "(each with a `publicationVenue`), not GitHub links.")
    st.sidebar.caption("**Fast** = rules only (no LLM). **Thorough** = the fast check plus an "
                       "LLM recommendation for each `review` paper. **Batch CSV** = one row "
                       "per paper, for scripts.")
    st.sidebar.caption("First time here? Open the **Tutorial** page above.")
    with st.sidebar.expander("⚙️ Environment", expanded=False):
        prov = os.getenv("LLM_PROVIDER", "azure")
        st.write(f"**LLM provider:** `{prov}`")
        model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "(unset)")
        st.write(f"**Model / deployment:** `{model}`")
        if model.lstrip().startswith("["):
            st.caption("Several names are configured; the check uses the first.")
        st.caption("Used by the **thorough** check only — the fast and batch checks need no "
                   "keys. Read from the repo's `.env` at startup; restart the app after "
                   "editing it.")


def get_papers():
    """Render the input widgets and return a parsed list of papers, or None.

    The widget keys are deliberately independent of the selected check, so
    switching between the three cards in Step 1 keeps the loaded papers.
    """
    st.caption("Input format: Semantic Scholar **paper records** (with `publicationVenue`). "
               "Upload a JSON **list**, or a **single** paper object.")
    with st.expander("See expected format"):
        st.code(_S2_EXAMPLE, language="json")
    sources = ["Upload file", "Paste JSON"]
    if _EXAMPLE_CORPUS.is_file():
        sources.append("Example corpus")
    mode = st.radio("Provide papers via", sources, horizontal=True, key="mode")
    raw = None
    if mode == "Upload file":
        up = st.file_uploader("papers.json", type=["json"], key="file")
        if up is not None:
            raw = up.getvalue().decode("utf-8")
    elif mode == "Paste JSON":
        raw = st.text_area("Paste JSON here", height=160, key="paste",
                           placeholder=_S2_EXAMPLE)
    else:
        st.caption(f"Using the bundled `examples/{_EXAMPLE_CORPUS.name}` — eight synthetic "
                   "records, exercising every verdict except `out_of_scope`. The expected "
                   "result is tabulated in `examples/README.md`.")
        try:
            raw = _EXAMPLE_CORPUS.read_text(encoding="utf-8")
        except OSError as e:
            st.error(f"Could not read the example corpus: {e}")
            return None
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
    # Everything as text: a sheet column here mixes numbers, strings and the
    # blanks left by merged cells, which Arrow cannot type (it logs a traceback
    # and falls back).  A preview of a formatted workbook is text anyway.
    data = [dict(zip(headers, [("" if v is None else str(v)) for v in r]))
            for r in rows[1:max_rows + 1]]
    st.dataframe(data)
    if len(rows) > max_rows + 1:
        st.caption(f"(showing first {max_rows} of {len(rows) - 1} rows — download for the full sheet)")


def render_result(res: runners.RunResult, checker: runners.Checker):
    if res.ok:
        st.success(f"✅ Done — {checker.title} finished. Your Excel is ready below.")
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


def _chip(text, bg):
    """A small pill. Dark text on a light fill, so it reads in either theme."""
    return (f"<span style='background:{bg};color:#111;padding:2px 10px;border-radius:12px;"
            f"margin:0 6px 6px 0;display:inline-block;font-size:0.8rem;'>{text}</span>")


_STATUS_ORDER = ("on_list", "review", "clean", "no_venue", "error")


def _status_summary(rows):
    """Coloured count-per-status chips (canonical colours), most-flagged first."""
    counts = {}
    for r in rows:
        counts[r["Status"]] = counts.get(r["Status"], 0) + 1
    ordered = [s for s in _STATUS_ORDER if s in counts] + \
              [s for s in counts if s not in _STATUS_ORDER]
    st.markdown("".join(_chip(f"{s}: <b>{counts[s]}</b>",
                              "#" + runners.STATUS_FILL_COLORS.get(s, "FFFFFF"))
                        for s in ordered), unsafe_allow_html=True)


def _csv_status_preview(rows, max_rows=100):
    """Preview the CSV with each Status cell shaded its canonical colour."""
    preview = rows[:max_rows]
    try:
        import pandas as pd
        df = pd.DataFrame(preview, columns=["SS-ID", "DOI", "Status"])

        def _bg(val):
            hexc = runners.STATUS_FILL_COLORS.get(val)
            return f"background-color: #{hexc}" if hexc else ""

        styler = df.style
        # pandas >= 2.1 renamed Styler.applymap -> Styler.map.
        styler = (styler.map(_bg, subset=["Status"]) if hasattr(styler, "map")
                  else styler.applymap(_bg, subset=["Status"]))
        st.dataframe(styler)
    except Exception:
        st.dataframe(preview)                             # plain fallback
    if len(rows) > max_rows:
        st.caption(f"(showing first {max_rows} of {len(rows)} rows — download for the full CSV)")


def render_csv_result(res: runners.BatchCsvResult):
    if res.ok:
        st.success(f"✅ Done — {len(res.rows)} record(s) classified. "
                   "Download the CSV (SS-ID, DOI, Status) below.")
        _status_summary(res.rows)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇️ Download CSV (SS-ID, DOI, Status)", data=res.csv_bytes,
                               file_name="bealls_status.csv", key="dl_csv", mime="text/csv")
        with c2:
            st.download_button("⬇️ Download colour-coded Excel", key="dl_csv_xlsx",
                               data=runners.build_status_xlsx(res.rows),
                               file_name="bealls_status.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        _csv_status_preview(res.rows)
    else:
        st.error(f"The batch check exited with code {res.returncode} and produced no CSV. "
                 "See the run log below.")
    with st.expander("Run log", expanded=not res.ok):
        if res.stdout:
            st.text(res.stdout[-8000:])
        if res.stderr:
            st.caption("stderr")
            st.text(res.stderr[-4000:])


# Step 1 is laid out as a comparison table: choose | check | what it does | and
# then one column per attribute the three checks actually differ on.  The point
# is that the attributes line up vertically, so comparing them is a scan down a
# column rather than a reading of three separate blocks.
_PICK_COLS = [1.25, 1.9, 4.3, 1.35, 1.3, 1.5]
_PICK_HEADERS = ("Check", "What it does", "API keys", "Tokens", "Returns")


def _cell(html):
    """A table cell: small text, uniform in every row."""
    return f"<div style='font-size:0.86rem;line-height:1.4;'>{html}</div>"


def _row_key(checker_id: str) -> str:
    """CSS-safe Streamlit key for a row container (ids contain dots)."""
    return "pickrow-" + checker_id.replace(".", "-")


# Selection is shown by colour — the whole row is tinted and its button turns
# solid — rather than by an accent bar in the margin, which would have to reserve
# space in every row or shift the text of the row that happens to be selected.
# Streamlit turns a container's ``key`` into the CSS class ``st-key-<key>``, so
# the tint can be applied to the row container itself, the gaps between its
# columns included.  The colour is the theme's primary red, translucent: strong
# enough to be unmistakable, light enough to leave the text at full contrast.
# The dark background needs a higher alpha to show the same amount of colour.
_ROW_TINT_CSS = """
<style>
div[class*="st-key-pickrow-"] {{
    transition: background-color 120ms ease, border-color 120ms ease;
}}
.st-key-{key} {{
    background-color: rgba(255, 75, 75, 0.10);
    border-color: rgba(255, 75, 75, 0.55);
}}
@media (prefers-color-scheme: dark) {{
    .st-key-{key} {{
        background-color: rgba(255, 75, 75, 0.18);
        border-color: rgba(255, 75, 75, 0.70);
    }}
}}
</style>
"""


def _pick_checker(checker_id: str):
    if st.session_state.get("checker_id") == checker_id:
        return                        # re-click on the selected row: nothing to do
    st.session_state["checker_id"] = checker_id
    # The add-on CSV belongs to a particular result; a new selection invalidates it.
    st.session_state.pop("csv_addon", None)


# ── Pages ────────────────────────────────────────────────────────────────
def page_check():
    """The four-step flow: choose a check, provide papers, set options, run."""
    st.title("🕵️ Beall's List predatory-venue check")
    st.caption("Is the paper's **journal/publisher on Beall's List** of potentially predatory "
               "venues? Add your papers as JSON, run, and download the Excel report. "
               "Step-by-step instructions are on the **Tutorial** page.")

    # Step 1 — choose a check, as a comparison table rather than a dropdown: the
    # three are meant to be weighed against each other, and a dropdown shows only the
    # option already chosen.
    st.markdown("**Step 1 · Choose a check**")
    st.caption("All three run the same deterministic engine. They differ in what they add "
               "on top of it and in what you get back.")
    if st.session_state.get("checker_id") not in runners.CHECKERS_BY_ID:
        st.session_state["checker_id"] = runners.CHECKERS[0].id

    # The header sits in a bordered container like the rows below it, so that the
    # container's padding is identical and the columns line up exactly.
    with st.container(border=True):
        header = st.columns(_PICK_COLS, vertical_alignment="bottom")
        for col, label in zip(header[1:], _PICK_HEADERS):
            col.markdown(f"<div style='font-size:0.72rem;font-weight:600;letter-spacing:0.05em;"
                         f"text-transform:uppercase;color:#6b7280;'>{label}</div>",
                         unsafe_allow_html=True)

    # The tint marking the selected row (see _ROW_TINT_CSS) names that row's
    # container class, so it is emitted once the selection is known.
    st.markdown(_ROW_TINT_CSS.format(key=_row_key(st.session_state["checker_id"])),
                unsafe_allow_html=True)

    for c in runners.CHECKERS:
        chosen = st.session_state["checker_id"] == c.id
        with st.container(border=True, key=_row_key(c.id)):
            row = st.columns(_PICK_COLS, vertical_alignment="center")
            with row[0]:
                # on_click sets the selection before the rerun renders, so the page
                # never lags a click behind.  The selected button stays enabled: a
                # disabled control greys out, and grey is the one colour that must
                # not mark the active choice.  Re-clicking it is a no-op.
                st.button("✓ Selected" if chosen else "Select", key=f"pick_{c.id}",
                          type="primary" if chosen else "secondary",
                          width="stretch", on_click=_pick_checker, args=(c.id,))
            row[1].markdown(_cell(f"<b>{c.icon} {c.title}</b><br>"
                                  f"<span style='color:#6b7280;font-size:0.78rem;'>"
                                  f"{c.tagline}</span>"), unsafe_allow_html=True)
            row[2].markdown(_cell(c.description), unsafe_allow_html=True)
            row[3].markdown(_cell(_chip("Required", "#FFD79A") if c.needs_llm
                                  else _chip("Not needed", "#C6EFCE")), unsafe_allow_html=True)
            row[4].markdown(_cell(_chip("Per venue", "#FFD79A") if c.needs_llm
                                  else _chip("None", "#C6EFCE")), unsafe_allow_html=True)
            row[5].markdown(_cell(_chip(c.output_label, "#E4E8EE")), unsafe_allow_html=True)
    checker = runners.CHECKERS_BY_ID[st.session_state["checker_id"]]
    if checker.needs_llm:
        with st.spinner("Checking the LLM configuration…"):
            llm_ok, llm_msg, llm_model = llm_status()
        if llm_ok is False:
            st.warning(f"⚠️ {llm_msg}")
        elif llm_ok:
            st.caption(f"🔑 LLM ready — `{os.getenv('LLM_PROVIDER', 'azure')}` / `{llm_model}`.")

    st.divider()

    # Step 2 — provide the papers
    st.markdown("**Step 2 · Provide the papers**")
    items = get_papers()
    if items is not None:
        st.success(f"✅ Loaded {len(items)} paper(s) — ready to run.")
        show_input_quality(items, key="input")

    st.divider()

    # Step 3 — options
    st.markdown("**Step 3 · Options** (optional)")
    with st.expander("⚙️ Whitelist, blacklist and Crossref"):
        if checker.kind == "csv":
            wl = bl = None
            st.caption("Batch CSV mode classifies **every** record — it has no whitelist/blacklist "
                       "scope filter (those are available on the fast and thorough checks).")
        else:
            st.markdown("**Scope — whitelist** (optional)")
            wl = venue_list_input("Only check papers from these venues — leave empty to check all",
                                  "wl")
            st.markdown("**Extend the list — blacklist** (optional)")
            bl = venue_list_input("Also treat papers from these venues as predatory", "bl")
            st.caption('Format for both boxes: a JSON list like '
                       '`[{"name": "IEEE", "domain": "ieee.org"}]` (a plain `"name"` string works too).')
        crossref = st.checkbox(
            "Cross-check each venue against Crossref (recommended; skipped automatically when offline)",
            value=True,
            key="cr",
            help="Crossref is the official registry of published articles (looked up by each "
                 "paper's DOI). When on (the default), the journal name and ISSN that Semantic "
                 "Scholar reports are compared against Crossref's official record, and DOIs are "
                 "used to resolve uncertain matches — catching papers where Semantic Scholar has "
                 "the wrong venue. It contacts Crossref once per paper (a little slower) and is "
                 "skipped automatically when there's no internet. Uncheck to force a fully offline run.")
    aux_lists = {"--whitelist": wl, "--blacklist": bl}
    extra_args = None if crossref else ["--crossref", "off"]

    st.divider()

    # Step 4 — run
    st.markdown("**Step 4 · Run**")
    summary = [f"{checker.icon} {checker.title}",
               f"{len(items)} paper(s)" if items else "no papers yet",
               "Crossref on" if crossref else "Crossref off"]
    summary += [name for name, lst in (("whitelist", wl), ("blacklist", bl)) if lst]
    st.caption(" · ".join(summary))
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
            with st.spinner(f"{checker.title} running… "
                            "large corpora and the LLM pass can take a while."):
                if checker.kind == "csv":
                    res = runners.run_batch_csv(items, crossref=crossref, on_line=on_line)
                else:
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
        st.session_state["res_checker_id"] = checker.id
        st.session_state.pop("csv_addon", None)      # belongs to the previous result
        notify("Check complete ✅" if res.ok else "Check failed 🛑",
               icon="✅" if res.ok else "🛑")

    res = st.session_state.get("res")
    if res is not None:
        # A result outlives a change of selection in Step 1, so it is labelled with
        # the check that actually produced it — never with whatever is selected now.
        ran = runners.CHECKERS_BY_ID.get(st.session_state.get("res_checker_id"), checker)
        st.divider()
        st.markdown("**Result**")
        if ran.id != checker.id:
            st.caption(f"This result came from **{ran.title}**. Run again to get a "
                       f"**{checker.title}** result.")
        if isinstance(res, runners.BatchCsvResult):
            render_csv_result(res)
        else:
            render_result(res, ran)

        # The machine-readable CSV, on demand, from a workbook result.  It cannot be
        # derived from the workbook: the batch check keeps every input record while
        # the workbook is deduplicated, so it is a second run of the deterministic
        # pass — offered here rather than done automatically, because that pass
        # repeats every Crossref lookup.
        if res.ok and ran.kind == "xlsx":
            st.markdown("**Also need it as a machine-readable list?**")
            st.caption("Runs the batch check over the same papers: `SS-ID, DOI, Status`, "
                       "**one row per input record in input order, with no deduplication** — "
                       "so it can have more rows than the workbook. Any whitelist or "
                       "blacklist is **not** applied, and neither is the LLM's verdict. The "
                       "deterministic pass, including its Crossref lookups, runs again.")
            if st.button("📄 Also produce the batch CSV", key="addon", disabled=items is None):
                with st.spinner("Batch check running…"):
                    st.session_state["csv_addon"] = runners.run_batch_csv(
                        items, crossref=crossref)
            addon = st.session_state.get("csv_addon")
            if addon is not None:
                render_csv_result(addon)


# A markdown image on a line of its own — Streamlit's markdown cannot load a
# local file, so these are rendered with st.image instead.
_MD_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", re.M)
# A link to a file in the repository. It resolves on GitHub but not in the app,
# where it is shown as the path it points at.
_MD_REL_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://|#)([^)]+)\)")


def _render_markdown_file(path):
    """Render a repository markdown file as a page, images and all."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        st.error(f"Could not read `{path.name}`: {exc}")
        return

    def chunk(md):
        md = md.strip()
        if md:
            st.markdown(_MD_REL_LINK.sub(r"\1 (`\2`)", md))

    pos = 0
    for m in _MD_IMAGE.finditer(text):
        chunk(text[pos:m.start()])
        img = (path.parent / m.group(2)).resolve()
        if img.is_file():
            st.image(str(img), caption=m.group(1) or None, width="stretch")
        pos = m.end()
    chunk(text[pos:])


def page_tutorial():
    """The walkthrough, rendered from ui/TUTORIAL.md so there is one copy of it."""
    _render_markdown_file(_TUTORIAL_MD)


# ── Navigation ──────────────────────────────────────────────────────────────
# Two pages in the sidebar: the flow, and the instructions for it.
nav = st.navigation([
    st.Page(page_check, title="Run a check", icon="▶️", default=True, url_path="check"),
    st.Page(page_tutorial, title="Tutorial", icon="📘", url_path="tutorial"),
])
sidebar()          # below the page links, on every page
nav.run()
