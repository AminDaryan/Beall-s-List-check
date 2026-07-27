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

import html
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
    """Sidebar content, below the page links: what this is, then the environment.

    Kept short on purpose.  The sidebar is on every page, so it holds only what
    is true on every page: what the tool answers, where the instructions are, and
    what the machine is configured with.  What used to be here besides — how the
    three checks differ, what shape the input takes — now lives where it is
    acted on, in Step 1's comparison table and Step 2's caption, rather than
    being said twice.

    The **Environment** panel comes before Step 1 simply by being in the sidebar,
    and its heading names the provider so the common question is answered without
    opening it.  It is not promoted into the flow itself: it applies to one of
    the three checks, and that check reports its own readiness in Step 1 the
    moment it is selected.
    """
    st.sidebar.markdown("### Beall's List check")
    st.sidebar.caption(
        "Is a paper's **journal or publisher** on Beall's List of *potentially* "
        "predatory venues? A match means the **venue appears on the list** — not "
        "that the paper is bad.")
    st.sidebar.caption("📘 New here? Open the **Tutorial** page above.")
    prov = os.getenv("LLM_PROVIDER", "azure")
    with st.sidebar.expander(f"⚙️ Environment · `{prov}`", expanded=False):
        st.write(f"**LLM provider:** `{prov}`")
        model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "(unset)")
        st.write(f"**Model / deployment:** `{model}`")
        if model.lstrip().startswith("["):
            st.caption("Several names are configured; the check uses the first.")
        st.caption("Used by the **thorough** check only — the fast and batch checks need no "
                   "keys. Read from the repo's `.env` at startup; restart the app after "
                   "editing it.")


def _load_example_corpus():
    """Put the bundled example corpus into the paste box (an ``on_click``).

    The example is not a way of *providing* papers, so it is not offered
    alongside "Upload a file" and "Paste JSON" — those two are your data, and it
    is not.  It fills the paste box instead, which needs no extra state, leaves
    the flow in a shape the user could have reached by hand, and has the side
    benefit of showing exactly what a corpus looks like.

    Widget state may be assigned from a callback: this runs before the rerun that
    rebuilds the widgets.
    """
    try:
        st.session_state["paste"] = _EXAMPLE_CORPUS.read_text(encoding="utf-8")
    except OSError as exc:
        notify(f"Could not read the example corpus: {exc}", icon="🛑")
        return
    st.session_state["mode"] = "Paste JSON"


def get_papers():
    """Render the input widgets and return a parsed list of papers, or None.

    The widget keys are deliberately independent of the selected check, so
    switching between the three rows in Step 1 keeps the loaded papers.
    """
    st.caption("Input format: Semantic Scholar **paper records** — JSON objects carrying a "
               "`publicationVenue`, not links or titles. A **list** of them, or a "
               "**single** one.")
    with st.expander("See expected format"):
        st.code(_S2_EXAMPLE, language="json")
    mode = st.radio("Provide papers via", ["Upload a file", "Paste JSON"],
                    horizontal=True, key="mode")
    raw = None
    if mode == "Upload a file":
        up = st.file_uploader("Papers file (.json)", type=["json"], key="file",
                              label_visibility="collapsed")
        if up is not None:
            raw = up.getvalue().decode("utf-8")
    else:
        raw = st.text_area("Paste JSON here", height=180, key="paste",
                           placeholder=_S2_EXAMPLE, label_visibility="collapsed")

    # The demo, kept clearly apart from the two real sources above.
    if _EXAMPLE_CORPUS.is_file():
        left, right = st.columns([2.4, 7.6], vertical_alignment="center")
        left.button("📦 Load the example corpus", key="loadexample", width="stretch",
                    on_click=_load_example_corpus)
        right.caption(f"No data to hand? Fills the paste box with `examples/"
                      f"{_EXAMPLE_CORPUS.name}` — eight synthetic records exercising every "
                      "verdict except `out_of_scope`, whose expected result is tabulated in "
                      "`examples/README.md`.")

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


def venue_list_input(label: str, key: str, hint: str = ""):
    """Optional JSON list of venues for whitelist/blacklist. Returns list or None."""
    raw = st.text_area(label, height=90, key=key, help=hint or None,
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


def _chip(text, tone="mute", *, title=None, flow=False):
    """A small pill in one of the palette's tones.

    *tone* is ``mute``, ``ok`` or ``warn`` — a class rather than a colour, so the
    pill picks up the light or dark value of that tone from :data:`_TOKENS`
    instead of carrying a hex that only works against one background.

    *title* becomes the browser's hover text — the sentence of detail that does
    not fit inside a pill — and marks the pill with the help cursor so there is
    something to invite the hover.  *flow* adds the trailing margins wanted when
    several pills wrap across a line; a pill alone in a table cell wants none,
    because the margin would push it off the cell's vertical centre.
    """
    cls = f"bl-chip bl-{tone}" + (" bl-flow" if flow else "")
    if not title:
        return f"<span class='{cls}'>{text}</span>"
    return (f"<span class='{cls} bl-help' "
            f"title='{html.escape(title, quote=True)}'>{text}</span>")


def readable_on(fill: str) -> str:
    """Slate-900 or white — whichever reads on the ``RRGGBB`` *fill*.

    The status fills are Excel's and are not ours to re-tone, but they are not
    uniform either: most are pale and want dark text, while ``error`` is a deep
    red that wants white.  Decided by WCAG relative luminance rather than by a
    hand-kept list, so a change to ``STATUS_FILL_COLORS`` cannot leave a chip
    unreadable.  The threshold is where the two contrast ratios meet.
    """
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(int(fill[i:i + 2], 16)) for i in (0, 2, 4))
    return "#0f172a" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.204 else "#ffffff"


def _status_chip(text, status, *, flow=False):
    """A pill in a *status*'s canonical colour, from ``STATUS_FILL_COLORS``.

    Separate from :func:`_chip` on purpose.  Those colours are the workbook's —
    the single source of truth is ``config.py`` — so they must not be re-toned
    for a dark background, or the chips would stop matching the file they
    describe.  Only the text on them is chosen here.
    """
    fill = runners.STATUS_FILL_COLORS.get(status, "FFFFFF")
    cls = "bl-chip" + (" bl-flow" if flow else "")
    return (f"<span class='{cls}' style='background:#{fill};"
            f"color:{readable_on(fill)};'>{text}</span>")


_STATUS_ORDER = ("on_list", "review", "clean", "no_venue", "error")


def _status_summary(rows):
    """Coloured count-per-status chips (canonical colours), most-flagged first."""
    counts = {}
    for r in rows:
        counts[r["Status"]] = counts.get(r["Status"], 0) + 1
    ordered = [s for s in _STATUS_ORDER if s in counts] + \
              [s for s in counts if s not in _STATUS_ORDER]
    st.markdown("".join(_status_chip(f"{s}: <b>{counts[s]}</b>", s, flow=True)
                        for s in ordered), unsafe_allow_html=True)


def _csv_status_preview(rows, max_rows=100):
    """Preview the CSV with each Status cell shaded its canonical colour."""
    preview = rows[:max_rows]
    try:
        import pandas as pd
        df = pd.DataFrame(preview, columns=["SS-ID", "DOI", "Status"])

        def _bg(val):
            hexc = runners.STATUS_FILL_COLORS.get(val)
            # The text colour has to be set alongside the fill.  These are pale
            # Excel fills, and the dark theme's own table text is near-white on
            # them; `error` is the mirror case, a deep red under dark text.
            return (f"background-color: #{hexc}; color: {readable_on(hexc)}"
                    if hexc else "")

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


# ── Step 1: the comparison table ─────────────────────────────────────────────
# Step 1 is laid out as a comparison table: choose | check | what it does | and
# then one column per attribute the three checks actually differ on.  The point
# is that the attributes line up vertically, so comparing them is a scan down a
# column rather than a reading of three separate blocks.
#
# A row is *two* Streamlit columns — the Select button, then everything else —
# and the "everything else" is a single element holding a five-column CSS grid.
# The five attributes are deliberately not five Streamlit columns: a grid centres
# its own cells and lines them up with the header row by construction, neither of
# which Streamlit's columns manage (see the stMarkdownContainer note in
# _PAGE_CSS).  Widths are `fr` units, so they behave like the column ratios they
# replace.
_PICK_COLS = [1.3, 8.9]
_PICK_GRID = "1.85fr 4.1fr 1.4fr 1.4fr 1.5fr"
_PICK_HEADERS = ("Check", "What it does", "Cost", "Records", "Returns")


def _grid(cells, head=False):
    """The five attribute cells of one comparison row, as a single CSS grid."""
    return (f"<div class='bl-grid{' bl-head' if head else ''}' "
            f"style='grid-template-columns:{_PICK_GRID};'>"
            + "".join(f"<div>{c}</div>" for c in cells) + "</div>")


def _row_key(checker_id: str) -> str:
    """CSS-safe Streamlit key for a row container (ids contain dots)."""
    return "pickrow-" + checker_id.replace(".", "-")


# Selection is shown by colour — the whole row is tinted and its button turns
# solid — rather than by an accent bar in the margin, which would have to reserve
# space in every row or shift the text of the row that happens to be selected.
# Streamlit turns a container's ``key`` into the CSS class ``st-key-<key>``, so
# the tint can be applied to the row container itself, the gaps between its
# columns included; the rule is re-emitted for the selected row on every rerun.
#
# The colour is the accent blue, translucent: strong enough to be unmistakable,
# light enough to leave the text at full contrast.  It is deliberately not red —
# red means `on_list` everywhere else in this tool, and a tinted row means
# "chosen", not "flagged".  The alphas come from the tokens, so the dark theme's
# stronger tint is defined in one place.
_ROW_TINT_CSS = """
<style>
.st-key-{key} {{
    background-color: var(--bl-accent-soft);
    border-color: var(--bl-accent-line) !important;
}}
</style>
"""


# ── Design tokens ────────────────────────────────────────────────────────────
# Every colour the page paints itself comes from the Tailwind CSS palette
# (https://tailwindcss.com/docs/colors) — an established, systematically
# generated, contrast-tested ramp — and is declared once, here, as a CSS custom
# property with a light and a dark value.  The step headers, the comparison
# table, the chips and the run summary therefore cannot drift apart, and none of
# them carries a hex that only works against one background.
#
# The same values are Streamlit's own theme in .streamlit/config.toml, which is
# what recolours the widgets this stylesheet does not reach — the buttons, the
# progress bar, links and focus rings.  Neutrals are *slate*, the accent is
# *blue*; red appears nowhere in the chrome, because red is a verdict here
# (`on_list`) and an accent that was also red made the page read as "flagged"
# where it only meant "selected".
#
# The media query is the only signal available: Streamlit exposes no attribute
# for the active theme and switches on `prefers-color-scheme` itself, so keying
# off the same thing keeps the two in step.
_TOKENS = """
:root {
  --bl-accent:#2563eb;                        /* blue-600           */
  --bl-on-accent:#ffffff;                     /* text on a solid accent fill */
  --bl-accent-soft:rgba(37,99,235,.07);
  --bl-accent-line:rgba(37,99,235,.55);
  /* Three weights of text, each a step of the slate ramp.  They start one step
     darker than the ramp's midpoint would suggest: the faintest of them has to
     clear 4.5:1 not against white but against the selected row's blue tint,
     and slate-500 there measured 4.32:1. */
  --bl-fg:#0f172a;                            /* slate-900, headings */
  --bl-fg-muted:#334155;                      /* slate-700, prose   */
  --bl-fg-faint:#475569;                      /* slate-600, labels  */
  --bl-surface:#f8fafc;                       /* slate-50           */
  --bl-border:#e2e8f0;                        /* slate-200          */
  --bl-ok-bg:#d1fae5;   --bl-ok-fg:#065f46;   /* emerald-100 / -800 */
  --bl-warn-bg:#fef3c7; --bl-warn-fg:#92400e; /* amber-100 / -800   */
  --bl-mute-bg:#e2e8f0; --bl-mute-fg:#334155; /* slate-200 / -700   */
}
@media (prefers-color-scheme: dark) {
  :root {
    --bl-accent:#60a5fa;                              /* blue-400   */
    /* A blue light enough to carry a dark background needs dark text on it:
       white on blue-400 is 2.5:1, well under the 4.5:1 that body-sized text
       has to clear.  Slate-900 on the same blue is 6.7:1. */
    --bl-on-accent:#0f172a;
    --bl-accent-soft:rgba(96,165,250,.12);
    --bl-accent-line:rgba(96,165,250,.60);
    --bl-fg:#f8fafc;                                  /* slate-50   */
    --bl-fg-muted:#cbd5e1;                            /* slate-300  */
    --bl-fg-faint:#94a3b8;                            /* slate-400  */
    --bl-surface:#1e293b;                             /* slate-800  */
    --bl-border:#334155;                              /* slate-700  */
    --bl-ok-bg:rgba(16,185,129,.18);   --bl-ok-fg:#6ee7b7;
    --bl-warn-bg:rgba(245,158,11,.18); --bl-warn-fg:#fcd34d;
    --bl-mute-bg:rgba(148,163,184,.18); --bl-mute-fg:#e2e8f0;
  }
}
"""


# The rest of the page's styling, emitted once per render.  What is here is what
# Streamlit's own arguments cannot reach: the step headers, the comparison grid,
# the run summary, the upload dropzone, and one correction to Streamlit itself.
_PAGE_CSS = "<style>" + _TOKENS + """
/* ── Step headers ──────────────────────────────────────────────────────────
   A numbered badge, the title, an optional hint, and a status pill pushed
   right.  The title is deliberately large: these four bars are the page's
   structure, and at body size they read as captions rather than as headings.
   Colours are explicit rather than an opacity on the body colour — an opacity
   dims the text against whatever is behind it, which is how the hint ended up
   too faint to read comfortably. */
.bl-step {display:flex; align-items:center; gap:.75rem; flex-wrap:wrap;
          padding:.75rem 1.05rem; border-radius:10px;
          background:var(--bl-surface);
          border:1px solid var(--bl-border);
          border-left:4px solid var(--bl-accent);}
.bl-step-n {flex:0 0 auto; width:2rem; height:2rem; border-radius:50%;
            display:inline-flex; align-items:center; justify-content:center;
            background:var(--bl-accent); color:var(--bl-on-accent);
            font-size:1rem; font-weight:700;}
.bl-step-t {font-size:1.3rem; font-weight:700; letter-spacing:-.01em;
            color:var(--bl-fg);}
.bl-step-h {font-size:.92rem; color:var(--bl-fg-muted);}
.bl-step-s {margin-left:auto; font-size:.82rem; font-weight:600;
            padding:3px 12px; border-radius:999px; white-space:nowrap;}

/* Each step header is rendered into its own keyed container, so the rhythm
   around it -- clear air above, a small gap to the step's own controls -- can be
   set here instead of relying on Streamlit's uniform 1rem block gap. */
div[class*="st-key-blstep"] {margin:2rem 0 .75rem;}
div[class*="st-key-blstep"] [data-testid="stMarkdownContainer"] {margin-bottom:0;}

/* ── Chips ─────────────────────────────────────────────────────────────────
   Tones, not colours, so each picks up its light or dark value from the tokens.
   The status chips are the exception and carry their own inline fill: it is the
   workbook's, and is the same in both themes (see _status_chip). */
.bl-chip {display:inline-block; padding:3px 11px; border-radius:999px;
          font-size:.8rem; font-weight:600; line-height:1.45;}
.bl-flow {margin:0 6px 6px 0;}
.bl-help {cursor:help;}
.bl-ok     {background:var(--bl-ok-bg);   color:var(--bl-ok-fg);}
.bl-warn   {background:var(--bl-warn-bg); color:var(--bl-warn-fg);}
.bl-mute, .bl-idle {background:var(--bl-mute-bg); color:var(--bl-mute-fg);}
.bl-wait   {background:var(--bl-warn-bg); color:var(--bl-warn-fg);}

/* ── The Step 1 comparison grid ────────────────────────────────────────────
   The header row and every check row are handed the same column template, so
   they line up without either knowing the other. */
.bl-grid {display:grid; gap:0 1rem; align-items:center;}
.bl-grid > div {font-size:.88rem; line-height:1.45; color:var(--bl-fg-muted);}
.bl-grid > div b {color:var(--bl-fg);}
.bl-head > div {font-size:.72rem; font-weight:700; letter-spacing:.06em;
                text-transform:uppercase; color:var(--bl-fg-faint);}
.bl-sub {font-size:.79rem; color:var(--bl-fg-faint);}
/* The header sits in a bordered container purely so its padding matches the
   rows' and the columns therefore align.  Only its bottom edge is wanted, and
   it is wanted: without a rule under them, "Check" and "What it does" float
   free of the rows they label instead of reading as a table head. */
.st-key-pickhead {border-color:transparent !important;
                  border-bottom:1px solid var(--bl-border) !important;
                  border-radius:0 !important;
                  padding-top:0 !important; padding-bottom:.45rem !important;
                  margin-bottom:-.4rem;}

/* ── The LLM-readiness slot ────────────────────────────────────────────────
   Always rendered, always one line high, whichever check is selected.  It used
   to appear only under the thorough check, which pushed Steps 2 to 4 down the
   moment that check was chosen.  Reserving the line costs 24px and removes the
   jump; a message too long for the line is clipped and carries the full text as
   hover text. */
.bl-llm {height:1.6rem; line-height:1.6rem; font-size:.85rem;
         color:var(--bl-fg-faint); margin-top:.35rem;
         white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
/* A fixed height, not a minimum: the "ready" message carries <code> spans, and
   their taller line box would otherwise still nudge the page by a pixel or two. */
.bl-llm code {line-height:1; font-size:.92em; padding:1px 5px;}
.bl-llm-bad {color:var(--bl-warn-fg); font-weight:600; cursor:help;}

/* ── Step 4's run summary ──────────────────────────────────────────────────
   A labelled cell per decision rather than a row of bare pills: "Fast check"
   and "Crossref on" say what they are, but "8 paper(s)" and a lone list name
   did not, and nothing said what would come back. */
.bl-sum {display:grid; grid-template-columns:repeat(auto-fit, minmax(8.5rem, 1fr));
         gap:.9rem 1.4rem; padding:.95rem 1.15rem;
         border:1px solid var(--bl-border); border-radius:10px;
         background:var(--bl-surface);}
.bl-sum-k {display:block; font-size:.7rem; font-weight:700; letter-spacing:.06em;
           text-transform:uppercase; color:var(--bl-fg-faint); margin-bottom:.2rem;}
.bl-sum-v {display:block; font-size:.95rem; font-weight:600; color:var(--bl-fg);}
.bl-sum-v.bl-sum-off {color:var(--bl-fg-faint); font-weight:500;}
.bl-sum-v.bl-sum-todo {color:var(--bl-warn-fg);}
.st-key-runsummary [data-testid="stMarkdownContainer"] {margin-bottom:0;}

/* ── The upload dropzone ───────────────────────────────────────────────────
   Streamlit's own is a 68px strip with a Browse button and a size limit, which
   does not say that a file can be dropped on it.  Turned here into a target
   worth aiming at: full width, tall, dashed, and captioned.  `column-reverse`
   is what puts the caption above the button -- the button comes first in the
   DOM -- and the caption's text has to be supplied by CSS because this version
   of Streamlit renders only the "200MB per file" line. */
[data-testid="stFileUploaderDropzone"] {
    flex-direction:column-reverse; align-items:center; justify-content:center;
    gap:.55rem; min-height:9.5rem; padding:1.5rem 1rem;
    border:2px dashed var(--bl-border) !important; border-radius:12px;
    background:var(--bl-surface);
    cursor:pointer; transition:border-color .15s ease, background-color .15s ease;}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color:var(--bl-accent) !important; background:var(--bl-accent-soft);}
[data-testid="stFileUploaderDropzone"]:focus-within {
    border-color:var(--bl-accent) !important;}
/* The two captions are pseudo-elements, so `order` is what puts them, the size
   limit and the button into a sensible reading order -- the DOM has the button
   first and the limit last, which is not the order they should be read in. */
[data-testid="stFileUploaderDropzoneInstructions"] {
    flex:0 0 auto; flex-direction:column; align-items:center; text-align:center;}
[data-testid="stFileUploaderDropzoneInstructions"]::before {
    order:1; content:"\\2b06\\fe0f  Drag and drop your papers .json here";
    font-size:1.05rem; font-weight:600; color:var(--bl-fg); margin-bottom:.1rem;}
[data-testid="stFileUploaderDropzoneInstructions"]::after {
    order:2; content:"or use the button below to browse";
    font-size:.85rem; color:var(--bl-fg-muted);}
[data-testid="stFileUploaderDropzoneInstructions"] > div {
    order:3; margin-top:.35rem; font-size:.75rem; color:var(--bl-fg-faint);}

/* The same point for Streamlit's own filled buttons -- Run, and the selected
   Select -- whose label it paints white against whatever primaryColor is.  In
   the dark theme that colour is blue-400, so the label is corrected here. */
@media (prefers-color-scheme: dark) {
  [data-testid="stBaseButton-primary"]:not(:disabled) {color:var(--bl-on-accent);}
}

/* ── A correction to Streamlit ─────────────────────────────────────────────
   Every stMarkdownContainer carries margin-bottom:-16px, to cancel the 16px gap
   between the elements of a vertical block.  A column holding one markdown
   element has no gap to cancel, but the negative margin still shortens the
   column's layout box by 16px while the text keeps its full height and quietly
   overflows it.  Streamlit then centres that too-short box, leaving the visible
   text 8px below the middle of the row -- beside a Select button that IS
   centred, so the row reads as misaligned.  Zero it out inside Step 1, where
   the two have to line up. */
div[class*="st-key-pickrow-"] [data-testid="stMarkdownContainer"],
.st-key-pickhead [data-testid="stMarkdownContainer"] {margin-bottom:0;}
</style>
"""


# The Tutorial page is long-form prose with full-width screenshots, and the app
# runs in Streamlit's `wide` layout.  Left alone, a line of text ran the whole
# window and a 3000px screenshot was scaled up to fill it, which made the images
# shout over the words they illustrate.  Both are capped here: the column to a
# comfortable measure, the images to something nearer their natural size, with
# a frame so a screenshot of a page reads as a picture of a page.
_TUTORIAL_CSS = "<style>" + _TOKENS + """
.st-key-tutorial {max-width:58rem; margin:0 auto;}
.st-key-tutorial [data-testid="stImage"] {max-width:40rem; margin:.4rem auto 1rem;}
.st-key-tutorial [data-testid="stImage"] img {border:1px solid var(--bl-border);
                                              border-radius:10px;}
.st-key-tutorial [data-testid="stImageCaption"] {color:var(--bl-fg-faint);
                                                 text-align:center;}
</style>
"""


def step_header(num, title, hint="", state="", tone="idle", into=None):
    """The header bar for one step of the flow.

    *state* is the step's live answer — the check chosen, the papers loaded, the
    options in force — shown as a pill on the right, so the state of the whole
    flow can be read while scrolling past it.  *tone* colours that pill: ``ok``
    green when the step is settled, ``wait`` amber when it is what holds the run
    up, ``idle`` grey when there is nothing to settle.  *into* renders into a
    container reserved earlier, for the steps whose state is only known once
    their own widgets have run.

    The header goes into a container keyed ``blstep<num>`` so the stylesheet can
    give it the air it needs above and below; *into* must be such a container.
    """
    st_ = into if into is not None else st.container(key=f"blstep{num}")
    st_.markdown(
        f"<div class='bl-step'><span class='bl-step-n'>{num}</span>"
        f"<span class='bl-step-t'>{title}</span>"
        + (f"<span class='bl-step-h'>{hint}</span>" if hint else "")
        + (f"<span class='bl-step-s bl-{tone}'>{state}</span>" if state else "")
        + "</div>", unsafe_allow_html=True)


def _options_state(checker):
    """Step 3's status pill: the options in force, named before they render.

    Read out of ``st.session_state`` rather than from the widgets, because the
    header is drawn above the expander that holds them.  Streamlit has already
    written this rerun's values there, so the pill is never a run behind.
    """
    bits = ["Crossref on" if st.session_state.get("cr", True) else "Crossref off"]
    if checker.kind != "csv":                 # batch mode ignores both lists
        bits += [name for name, key in (("whitelist", "wl"), ("blacklist", "bl"))
                 if (st.session_state.get(key) or "").strip()]
    return " · ".join(bits)


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
    st.markdown(_PAGE_CSS, unsafe_allow_html=True)

    if st.session_state.get("checker_id") not in runners.CHECKERS_BY_ID:
        st.session_state["checker_id"] = runners.CHECKERS[0].id
    checker = runners.CHECKERS_BY_ID[st.session_state["checker_id"]]

    # Step 1 — choose a check.  A comparison table rather than a dropdown: the
    # three are meant to be weighed against each other, and a dropdown shows only
    # the option already chosen.
    step_header(1, "Choose a check", "what to run over your papers",
                f"{checker.icon} {checker.title}", "ok")
    st.caption("All three run the same deterministic engine. They differ in what they add "
               "on top of it, what they cost, and what you get back. "
               "Hover a **Cost**, **Records** or **Returns** pill for the detail.")

    # The header sits in a bordered container like the rows below it, so that the
    # container's padding is identical and the columns line up exactly.
    with st.container(border=True, key="pickhead"):
        st.columns(_PICK_COLS)[1].markdown(_grid(_PICK_HEADERS, head=True),
                                           unsafe_allow_html=True)

    # The tint names the selected row's container class, so it is emitted once
    # the selection for this rerun is known.
    st.markdown(_ROW_TINT_CSS.format(key=_row_key(checker.id)), unsafe_allow_html=True)

    for c in runners.CHECKERS:
        chosen = c.id == checker.id
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
            row[1].markdown(_grid((
                f"<b>{c.icon} {c.title}</b><br><span class='bl-sub'>{c.tagline}</span>",
                c.description,
                _chip(c.cost_label, "warn" if c.needs_llm else "ok", title=c.cost_note),
                _chip(c.records_label, "mute", title=c.records_note),
                _chip(c.output_label, "mute", title=c.output_note),
            )), unsafe_allow_html=True)

    # The LLM-readiness line.  Its slot is rendered whatever is selected and is
    # always exactly one line high, so choosing the thorough check no longer
    # shoves Steps 2 to 4 down the page.  Written twice into the same element —
    # once to say the check is running, once with its answer — so the notice
    # never appears or disappears, only changes its text.
    llm_slot = st.empty()
    llm_html = "&nbsp;"
    if checker.needs_llm:
        llm_slot.markdown("<div class='bl-llm'>⏳ Checking the LLM configuration…</div>",
                          unsafe_allow_html=True)
        llm_ok, llm_msg, llm_model = llm_status()
        if llm_ok:
            llm_html = (f"🔑 LLM ready — <code>{html.escape(os.getenv('LLM_PROVIDER', 'azure'))}"
                        f"</code> / <code>{html.escape(llm_model)}</code>.")
        else:
            lead = "LLM not configured" if llm_ok is False else "LLM configuration unverified"
            llm_html = (f"<span class='bl-llm-bad' title='{html.escape(llm_msg, quote=True)}'>"
                        f"⚠️ {lead} — {html.escape(llm_msg)}</span>")
    llm_slot.markdown(f"<div class='bl-llm'>{llm_html}</div>", unsafe_allow_html=True)

    # Step 2 — provide the papers.  The header's container is reserved first and
    # written afterwards: how many papers there are is only known once the widgets
    # below it have run, and the pill would otherwise report the previous rerun.
    head2 = st.container(key="blstep2")
    items = get_papers()
    step_header(2, "Provide the papers", "Semantic Scholar records, as JSON",
                f"✅ {len(items)} paper(s)" if items else "waiting for papers",
                "ok" if items else "wait", into=head2)
    if items is not None:
        st.success(f"✅ Loaded {len(items)} paper(s) — ready to run.")
        show_input_quality(items, key="input")

    # Step 3 — options.  All optional, so the pill states what is in force rather
    # than whether anything was touched.
    #
    # Crossref is not in the expander with the other two.  It is one checkbox, it
    # is on by default, and it changes verdicts — two of the example corpus's
    # eight records fall back from `on_list` to `review` without it — so it is
    # worth a line of the page.  The venue lists are genuinely advanced, are
    # empty on almost every run, and cost two text areas to show, so they stay
    # folded away.
    step_header(3, "Options", "all optional", _options_state(checker))
    crossref = st.checkbox(
        "Cross-check every venue against Crossref",
        value=True,
        key="cr",
        help="Crossref is the official registry of published articles (looked up by each "
             "paper's DOI). When on (the default), the journal name and ISSN that Semantic "
             "Scholar reports are compared against Crossref's official record, and DOIs are "
             "used to resolve uncertain matches — catching papers where Semantic Scholar has "
             "the wrong venue. It contacts Crossref once per paper (a little slower) and is "
             "skipped automatically when there's no internet. Uncheck to force a fully offline run.")
    st.caption("Recommended. Confirms each venue against the official registry using the paper's "
               "DOI, which resolves matches the metadata alone leaves uncertain. Adds one lookup "
               "per paper, and is skipped automatically when there is no internet.")
    with st.expander("🎯 Advanced — narrow the run, or add venues of your own"):
        if checker.kind == "csv":
            wl = bl = None
            st.caption("Batch CSV classifies **every** record, so it takes neither list. "
                       "Both are available on the fast and thorough checks.")
        else:
            # Named by what they do, with the familiar term kept as a secondary
            # identifier.  "Whitelist" alone is ambiguous here: it reads just as
            # easily as "venues I consider safe", which is the opposite of what
            # it does.
            wl = venue_list_input(
                "Only check these venues  ·  **whitelist**", "wl",
                hint="Restricts the run. A paper from any venue not listed here is reported as "
                     "`out_of_scope` rather than checked. Leave empty to check every paper.")
            bl = venue_list_input(
                "Also treat these venues as predatory  ·  **blacklist**", "bl",
                hint="Extends the list. These venues count as predatory for this run in "
                     "addition to everything on Beall's List.")
            st.caption('Both boxes take the same JSON: a list like '
                       '`[{"name": "IEEE", "domain": "ieee.org"}]`, or just `"Nature"` to match '
                       'by name.')
    aux_lists = {"--whitelist": wl, "--blacklist": bl}
    extra_args = None if crossref else ["--crossref", "off"]

    # Step 4 — run.  The summary restates every decision made above, each under
    # the name of the thing it decides, so what is about to run can be checked
    # without scrolling back up — and without having to work out what a bare
    # "Merged" or "8 paper(s)" was answering.
    step_header(4, "Run", "everything above, in one go",
                "ready" if items else "add papers first", "ok" if items else "wait")
    lists_na = checker.kind == "csv"
    summary = [
        ("Check", f"{checker.icon} {checker.title}", ""),
        ("Papers", f"{len(items)} loaded" if items else "none yet",
         "" if items else "bl-sum-todo"),
        ("Crossref", "On" if crossref else "Off", "" if crossref else "bl-sum-off"),
        ("Whitelist", "not used here" if lists_na else
         (f"{len(wl)} venue(s)" if wl else "—"),
         "bl-sum-off" if lists_na or not wl else ""),
        ("Blacklist", "not used here" if lists_na else
         (f"{len(bl)} venue(s)" if bl else "—"),
         "bl-sum-off" if lists_na or not bl else ""),
        ("You get back", checker.output_label, ""),
    ]
    # Keyed so that the screenshot script can wait on this summary — the one place
    # that states which check a press of Run would actually start.
    with st.container(key="runsummary"):
        st.markdown("<div class='bl-sum'>" + "".join(
            f"<div><span class='bl-sum-k'>{k}</span>"
            f"<span class='bl-sum-v {cls}'>{v}</span></div>"
            for k, v, cls in summary) + "</div>", unsafe_allow_html=True)
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
    """The walkthrough, rendered from ui/TUTORIAL.md so there is one copy of it.

    Rendered into a keyed container so the stylesheet can hold the text to a
    readable measure and the screenshots to something near their natural size,
    instead of letting both run the full width of a `wide` layout.
    """
    st.markdown(_TUTORIAL_CSS, unsafe_allow_html=True)
    with st.container(key="tutorial"):
        _render_markdown_file(_TUTORIAL_MD)


# ── Navigation ──────────────────────────────────────────────────────────────
# Two pages in the sidebar: the flow, and the instructions for it.
nav = st.navigation([
    # The default page is served from "/", so it is given no url_path of its own.
    st.Page(page_check, title="Run a check", icon="▶️", default=True),
    st.Page(page_tutorial, title="Tutorial", icon="📘", url_path="tutorial"),
])
sidebar()          # below the page links, on every page
nav.run()
