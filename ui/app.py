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


# The provider `.env` names. Step 3 starts from it and can be pointed elsewhere
# for a single run, so it is a default rather than the answer.
ENV_PROVIDER = os.getenv("LLM_PROVIDER", "azure").lower()

# The last entry of Step 3's model list, and its escape hatch. A model this
# machine's .env has not been told about cannot be offered in a list, but that is
# no reason to refuse to run it: choosing this reveals a text box instead.
_OTHER_MODEL = "Other — type a name…"


@st.cache_data(show_spinner=False)
def llm_status(provider):
    """Whether *provider* is usable: ``(ok, message, models)``.

    Answered by the same ``validate_provider_config()`` the checks call, so the
    thorough check cannot be reported as ready and then fail on the first
    request.  ``ok is None`` means the question could not be answered — the
    caller stays quiet rather than guessing.

    Asked about a named provider rather than about "the" provider: Step 3
    chooses one per run, and a readiness line that answered for ``.env``'s while
    the run used another would be worse than none at all.

    *models* is every model name that provider is configured with, in order,
    taken from ``openai_client`` rather than re-parsed here: the variables
    accept a bare name, a comma-separated list or a JSON array, and one parser
    for that is enough.  Step 3 offers the list as a choice; the check itself
    reads whichever single name the UI puts back into its environment.

    Deliberately not called on every page load: it imports litellm, which costs
    a few seconds, and only the thorough check needs the answer.
    """
    try:
        import openai_client
    except Exception as exc:
        return None, str(exc), []
    models = openai_client.provider_models(provider)
    try:
        openai_client.validate_provider_config(provider)
        return True, "", models
    except Exception as exc:
        return False, str(exc), models


@st.cache_data(show_spinner=False)
def llm_providers():
    """The providers Step 3 offers, in the order it offers them.

    ``.env``'s own value is included even when it is not one of the documented
    names: ``openai_client`` passes an unrecognised provider through to litellm
    untouched, so a backend nobody here has written down still runs, and a
    dropdown that dropped it would silently switch the run to something else.
    """
    try:
        import openai_client
        names = list(openai_client.SUPPORTED_PROVIDERS)
    except Exception:
        names = ["azure", "openai", "anthropic", "mistral", "gemini", "ollama"]
    if ENV_PROVIDER not in names:
        names.insert(0, ENV_PROVIDER)
    return names


def selected_provider():
    """The provider Step 3 has selected, readable before Step 3 has rendered.

    Read out of ``st.session_state`` for the same reason ``_options_state`` is:
    Step 1's readiness line sits above the widget that decides this, and
    Streamlit has already written this rerun's value there.
    """
    return st.session_state.get("provider") or ENV_PROVIDER


def selected_model():
    """The model Step 3 has selected — a configured name, or one typed in.

    Empty when nothing is chosen, which is a state the flow reports rather than
    hides: the run is started with whatever this returns, and with nothing it
    falls back to what the environment already names.
    """
    name = st.session_state.get("model") or ""
    if name == _OTHER_MODEL:
        name = st.session_state.get("model_custom") or ""
    return name.strip()


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

    What it reports is what ``.env`` configures — the **defaults** a run starts
    from, not what a run necessarily uses.  Both the provider and the model are
    picked in Step 3, which is where they are decided and where they are shown.
    """
    st.sidebar.markdown("### Beall's List check")
    st.sidebar.caption(
        "Is a paper's **journal or publisher** on Beall's List of *potentially* "
        "predatory venues? A match means the **venue appears on the list** — not "
        "that the paper is bad.")
    st.sidebar.caption("📘 New here? Open the **Tutorial** page above.")
    with st.sidebar.expander(f"⚙️ Environment · `{ENV_PROVIDER}`", expanded=False):
        st.write(f"**LLM provider:** `{ENV_PROVIDER}`")
        st.write(f"**Model / deployment:** `{os.getenv('AZURE_OPENAI_DEPLOYMENT', '(unset)')}`")
        st.caption("The **defaults**. Both are chosen per run in **Step 3**, which can point a "
                   "single run at another provider or another model without this changing — "
                   "and only the **thorough** check uses either, the fast and batch checks "
                   "needing no keys at all. Read from the repo's `.env` at startup; restart "
                   "the app after editing it.")


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
    # Collapsed, always.  This panel reports on the *metadata that was supplied*,
    # not on anything the tool has done, and it is normal for a real corpus to
    # have some — the example corpus is deliberately one of those, because record
    # 6 carries no venue at all.  Opening itself and leading with a warning made
    # a green "Loaded 8 paper(s) — ready to run" be immediately contradicted by
    # what looked like an error.  The count is in the label, which is the part
    # worth seeing; the detail is one click away for whoever wants it.
    icon = "⚠️" if flagged else "✅"
    label = (f"{icon} Input data quality — {len(flagged)} of {len(items)} record(s) flagged"
             if flagged else
             f"✅ Input data quality — nothing flagged in {len(items)} record(s)")
    with st.expander(label, expanded=False):
        st.caption("An offline look at the Semantic Scholar metadata *before* the check runs. "
                   "A flag means a record is missing something the venue match relies on, so "
                   "its verdict may be weaker — it does not stop the run and is not an error.")
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
# Every colour the page paints itself is declared once, here, as a CSS custom
# property, so the step headers, the comparison table, the chips and the run
# summary cannot drift apart.  Hues come from the Tailwind CSS palette
# (https://tailwindcss.com/docs/colors) — an established, systematically
# generated, contrast-tested ramp.  Neutrals are *slate*, the accent is *blue*;
# red appears nowhere in the chrome, because red is a verdict here (`on_list`)
# and an accent that was also red made the page read as "flagged" where it only
# meant "selected".
#
# Nothing here branches on light versus dark, and that is the point.  Streamlit
# has a theme switcher (⋮ → System / Light / Dark) that repaints the app without
# touching `prefers-color-scheme`, and it exposes no attribute naming the active
# theme.  An earlier version of this stylesheet keyed off the media query, so a
# user whose browser preferred dark and who then chose **Light** in that menu got
# Streamlit's white page with this file's dark-theme text on it — light grey on
# white, barely readable.  There is no signal that would have fixed it.
#
# So the tokens are written not to need one:
#
#   * text is `currentColor` thinned by `color-mix`, which resolves against
#     whatever colour the active theme is already using, so every level keeps its
#     ratio to a background this file never has to know;
#   * surfaces and borders are translucent overlays of that same colour — they
#     darken a light page and lighten a dark one, by construction;
#   * the semantic fills are a translucent wash of one hue with the theme's own
#     text laid over them, so the hue carries the meaning and the theme carries
#     the contrast;
#   * the accent is a single opaque blue-500.  Being opaque, the page behind it
#     is irrelevant: the fill clears 3:1 against both white and slate-900, and
#     slate-900 on the fill clears 4.8:1 either way.
#
# Streamlit's own widgets are a separate matter and need no help — it knows which
# theme is active, so the [theme] / [theme.dark] pair in .streamlit/config.toml
# is applied correctly however the theme was chosen.
_TOKENS = """
:root {
  /* One accent, opaque, valid on any background (see above). */
  --bl-accent:#3b82f6;                 /* blue-500 */
  --bl-on-accent:#0f172a;              /* slate-900, 4.8:1 on the fill */
  --bl-accent-soft:rgba(59,130,246,.13);
  --bl-accent-line:rgba(59,130,246,.60);

  /* Three weights of the theme's own text.  The percentages are chosen so the
     faintest still clears 4.5:1 in both of Streamlit's themes. */
  --bl-fg:currentColor;
  --bl-fg-muted:color-mix(in srgb, currentColor 82%, transparent);
  --bl-fg-faint:color-mix(in srgb, currentColor 66%, transparent);

  /* Overlays of the text colour: a tint on a light page, a lift on a dark one. */
  --bl-surface:color-mix(in srgb, currentColor 5%, transparent);
  --bl-border:color-mix(in srgb, currentColor 20%, transparent);

  /* Hue carries the meaning; the theme's text carries the contrast. */
  --bl-ok-bg:rgba(16,185,129,.22);     /* emerald-500 */
  --bl-warn-bg:rgba(245,158,11,.25);   /* amber-500   */
  --bl-mute-bg:color-mix(in srgb, currentColor 13%, transparent);

  /* A gear, as a mask rather than an <img>, so it inherits the toolbar's colour
     and therefore needs no light/dark pair either. Material Symbols "settings". */
  --bl-gear:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg'\
 viewBox='0 -960 960 960'%3E%3Cpath d='m370-80-16-128q-13-5-24.5-12T307-235l-119 50L78-375l103-78q-1-7-1-13.5v-27q0\
-6.5 1-13.5L78-585l110-190 119 50q11-8 23-15t24-12l16-128h220l16 128q13 5 24.5 12t22.5 15l119-50 110 190-103 78q1 7\
 1 13.5v27q0 6.5-2 13.5l103 78-110 190-118-50q-11 8-23 15t-24 12L590-80H370Zm112-260q58 0 99-41t41-99q0-58-41-99t-99\
-41q-59 0-99.5 41T342-480q0 58 40.5 99t99.5 41Z'/%3E%3C/svg%3E");
}
"""


# Corrections to Streamlit's own chrome, on every page of the app.
#
# The code blocks are the substantial one.  Streamlit paints its syntax
# highlighting in the *same* colours whichever theme is active — measured, they
# are identical — and against its light code background those colours land at
# 2.5:1 to 2.9:1, far under the 4.5:1 body text has to clear.  Numbers, strings
# and JSON keys were the least readable text in the app, which matters here
# because the tutorial and the expected-format panel are mostly JSON.
#
# They cannot be fixed by choosing better colours alone: a colour that clears
# 4.5:1 against white needs a luminance at or below 0.183, and one that clears it
# against slate-900 needs 0.228 or above, so no single value can do both, and the
# active theme is not knowable here (see _TOKENS).  Pinning the code background
# dark resolves it — a dark code block on a light page is ordinary in
# documentation — after which one set of light token colours serves both themes.
_CHROME_CSS = """
/* Code blocks: always dark, so the token colours below have one background to
   be legible against. Ratios are on #0f172a. */
[data-testid="stCode"] pre, .stCode pre, .stMarkdown pre {
    background:#0f172a !important; border:1px solid #1e293b; border-radius:8px;}
[data-testid="stCode"] pre code, .stMarkdown pre code {color:#e2e8f0 !important;}
[data-testid="stCode"] pre .token, .stMarkdown pre .token {background:none !important;}
.stMarkdown pre .token.property,    [data-testid="stCode"] pre .token.property,
.stMarkdown pre .token.attr-name,   [data-testid="stCode"] pre .token.attr-name
    {color:#7dd3fc !important;}     /* sky-300      — 10.4:1 */
.stMarkdown pre .token.string,      [data-testid="stCode"] pre .token.string,
.stMarkdown pre .token.attr-value,  [data-testid="stCode"] pre .token.attr-value
    {color:#6ee7b7 !important;}     /* emerald-300  — 11.2:1 */
.stMarkdown pre .token.number,      [data-testid="stCode"] pre .token.number,
.stMarkdown pre .token.boolean,     [data-testid="stCode"] pre .token.boolean
    {color:#5eead4 !important;}     /* teal-300     — 11.8:1 */
.stMarkdown pre .token.keyword,     [data-testid="stCode"] pre .token.keyword,
.stMarkdown pre .token.function,    [data-testid="stCode"] pre .token.function
    {color:#93c5fd !important;}     /* blue-300     —  9.0:1 */
.stMarkdown pre .token.comment,     [data-testid="stCode"] pre .token.comment
    {color:#94a3b8 !important; font-style:italic;}
.stMarkdown pre .token.operator,    [data-testid="stCode"] pre .token.operator,
.stMarkdown pre .token.punctuation, [data-testid="stCode"] pre .token.punctuation
    {color:#cbd5e1 !important;}     /* slate-300    — 12.4:1 */

/* Inline `code` was Streamlit's green, which belongs to no palette here and is
   the same green in both themes.  A wash of the theme's own text needs neither. */
.stMarkdown code:not(pre code), [data-testid="stCaptionContainer"] code,
[data-testid="stMarkdownContainer"] code:not(pre code) {
    color:inherit !important;
    background:color-mix(in srgb, currentColor 11%, transparent) !important;
    padding:1px 5px; border-radius:5px;}

/* The toolbar's only remaining control opens Settings (the light/dark override),
   Print and About -- so it is drawn as a gear rather than as an overflow "⋮",
   which promised a menu of actions there is no longer one of.  The Deploy button
   that sat beside it is removed in .streamlit/config.toml, not here. */
[data-testid="stMainMenuButton"] svg {display:none;}
[data-testid="stMainMenuButton"]::after {
    content:""; width:1.25rem; height:1.25rem; display:block;
    background:currentColor;
    -webkit-mask:var(--bl-gear) center/contain no-repeat;
            mask:var(--bl-gear) center/contain no-repeat;}
"""

# The rest of the page's styling, emitted once per render.  What is here is what
# Streamlit's own arguments cannot reach: the step headers, the comparison grid,
# the run summary, the upload dropzone, and one correction to Streamlit itself.
_PAGE_CSS = "<style>" + _TOKENS + _CHROME_CSS + """
/* ── Step headers ──────────────────────────────────────────────────────────
   A numbered badge, the title, an optional hint, and a status pill pushed
   right.  The title is deliberately large: these four bars are the page's
   structure, and at body size they read as captions rather than as headings.
   The hint is thinned from the theme's own text rather than dimmed by opacity:
   an opacity fades toward whatever happens to be behind, which is how it ended
   up too faint to read comfortably. */
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
/* The ⓘ into the Tutorial. Sized as a target rather than as a glyph, and kept
   quiet until hovered so it does not compete with the step's own title. */
.bl-info {flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center;
          width:1.5rem; height:1.5rem; margin-left:-.35rem; border-radius:50%;
          font-size:1.05rem; line-height:1; text-decoration:none !important;
          color:var(--bl-fg-faint); transition:color .15s ease, background-color .15s ease;}
.bl-info:hover {color:var(--bl-accent); background:var(--bl-accent-soft);}

/* Each step header is rendered into its own keyed container, so the rhythm
   around it -- clear air above, a small gap to the step's own controls -- can be
   set here instead of relying on Streamlit's uniform 1rem block gap. */
div[class*="st-key-blstep"] {margin:2rem 0 .75rem;}
div[class*="st-key-blstep"] [data-testid="stMarkdownContainer"] {margin-bottom:0;}

/* ── Chips ─────────────────────────────────────────────────────────────────
   A tone is a wash, not a text colour: the fill carries the meaning and the
   theme's own text is laid over it, which is what lets one rule serve both
   themes.  The status chips are the exception and carry an inline fill — the
   workbook's canonical colour, with its text picked by luminance
   (see _status_chip). */
.bl-chip {display:inline-block; padding:3px 11px; border-radius:999px;
          font-size:.8rem; font-weight:600; line-height:1.45;}
.bl-flow {margin:0 6px 6px 0;}
.bl-help {cursor:help;}
.bl-ok     {background:var(--bl-ok-bg);}
.bl-warn, .bl-wait {background:var(--bl-warn-bg);}
.bl-mute, .bl-idle {background:var(--bl-mute-bg);}

/* ── The Step 1 comparison grid ────────────────────────────────────────────
   The header row and every check row are handed the same column template, so
   they line up without either knowing the other.

   The thinned text colours are applied to leaves only, never to a cell that
   contains another one.  They are fractions of `currentColor`, so nesting them
   multiplies: 82% inside 82% is 67%, and the tagline under each check name — a
   .bl-sub inside a muted cell — came out at 3.3:1 that way. */
.bl-grid {display:grid; gap:0 1rem; align-items:center;}
.bl-grid > div {font-size:.88rem; line-height:1.45;}
.bl-grid > div:nth-child(2) {color:var(--bl-fg-muted);}   /* "What it does" only */
.bl-head > div {font-size:.72rem; font-weight:700; letter-spacing:.06em;
                text-transform:uppercase; color:var(--bl-fg-faint);}
.bl-head > div:nth-child(2) {color:var(--bl-fg-faint);}
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
/* Marked by a wash and the ⚠, not by a coloured text: no single amber clears
   4.5:1 on both Streamlit's white and its slate-900 -- the two requirements are
   arithmetically incompatible -- so the theme's own text is used instead. */
.bl-llm-bad {background:var(--bl-warn-bg); border-radius:6px;
             padding:1px 8px; font-weight:600; cursor:help;}

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
.bl-sum-v.bl-sum-todo {display:inline-block; background:var(--bl-warn-bg);
                       border-radius:6px; padding:0 8px;}
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
/* `inherit`, not the token again: the token is a fraction of currentColor, so
   re-applying it to the descendants would thin an already-thinned colour. */
[data-testid="stFileUploaderDropzoneInstructions"] > div * {color:inherit;}

/* Streamlit fades a disabled button's label to 40% of the theme's text colour,
   which left "▶ Run check" at 2.4:1 for as long as it waits for papers.
   Inactive controls are exempt from the contrast rules, but the label is what
   names the action that is waiting, so it is lifted.
   Slate-500, flat.  It cannot be a fraction of currentColor like the rest of
   this stylesheet, because the colour it would take a fraction OF is Streamlit's
   already-faded one, applied to the button itself — and the label sits four
   elements below that, so every level would thin it again.  4.8:1 on
   Streamlit's white and 3.6:1 on its slate-900, against 2.4:1 and 2.1:1. */
[data-testid="stBaseButton-primary"]:disabled,
[data-testid="stBaseButton-primary"]:disabled * {color:#64748b !important;}

/* Streamlit paints its filled buttons' labels white on whatever primaryColor is.
   White on blue-500 is 3.5:1; slate-900 on it is 4.8:1. */
[data-testid="stBaseButton-primary"]:not(:disabled) {color:var(--bl-on-accent);}

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
_TUTORIAL_CSS = "<style>" + _TOKENS + _CHROME_CSS + """
.st-key-tutorial {max-width:58rem; margin:0 auto;}

/* ── Section headings ──────────────────────────────────────────────────────
   The numbers are the walkthrough's spine, so they are given the same filled
   badge the flow's own steps carry rather than being left as the first two
   characters of a heading.

   The flex sits on the span *inside* the heading, not on the heading: Streamlit
   rewraps a heading's content in a span of its own and appends the little
   link-to-heading anchor as a sibling, so laying the heading out as a flex row
   would put that anchor in the row too. */
.st-key-tutorial .bl-h {margin:2.4rem 0 .9rem; scroll-margin-top:4rem;}
.st-key-tutorial .bl-h > span:first-child {display:inline-flex; align-items:center;
                                           gap:.7rem;}
.st-key-tutorial h3.bl-h {margin:1.8rem 0 .7rem;}
.st-key-tutorial .bl-h-n {flex:0 0 auto; width:2.1rem; height:2.1rem; border-radius:50%;
                          display:inline-flex; align-items:center; justify-content:center;
                          background:var(--bl-accent); color:var(--bl-on-accent);
                          font-size:1.05rem; font-weight:700;}
.st-key-tutorial h3.bl-h .bl-h-n {width:1.7rem; height:1.7rem; font-size:.9rem;}
.st-key-tutorial .bl-h-plain {display:block; scroll-margin-top:4rem;}

/* ── Contents ──────────────────────────────────────────────────────────────
   A menu of rows, not a strip of chips.  A contents list is navigation, and
   navigation is read down a column; as chips it wrapped differently at every
   window width, truncated nothing but promised a pill's worth of room for
   titles that need a line, and gave each entry a target the size of its text. */
.bl-toc {margin:0 0 1.75rem; border:1px solid var(--bl-border); border-radius:10px;
         background:var(--bl-surface); overflow:hidden;}
.bl-toc-k {padding:.55rem 1rem; font-size:.7rem; font-weight:700; letter-spacing:.06em;
           text-transform:uppercase; color:var(--bl-fg-faint);
           border-bottom:1px solid var(--bl-border);}
.bl-toc-row {display:flex; align-items:center; gap:.8rem; padding:.55rem 1rem;
             color:inherit !important; text-decoration:none !important;
             border-bottom:1px solid var(--bl-border);
             transition:background-color .12s ease, box-shadow .12s ease;}
.bl-toc-row:last-child {border-bottom:0;}
/* The hover is a fill and a rule down the left edge, not a colour on the text:
   the accent is a 3.7:1 blue, which is fine for a bar and short of what text at
   this size has to clear. */
.bl-toc-row:hover {background:var(--bl-accent-soft);
                   box-shadow:inset 3px 0 0 var(--bl-accent);}
.bl-toc-n {display:inline-flex; align-items:center; justify-content:center;
           width:1.55rem; height:1.55rem; border-radius:50%; flex:0 0 auto;
           background:var(--bl-accent); color:var(--bl-on-accent);
           font-size:.78rem; font-weight:700;}
/* Muted rather than faint: the dot sits on a wash, and the wash costs it enough
   contrast that the faint level landed at 4.3:1. */
.bl-toc-n-plain {background:var(--bl-mute-bg); color:var(--bl-fg-muted);}
.bl-toc-t {font-size:.93rem;}

/* ── Screenshots ───────────────────────────────────────────────────────────
   Centred, framed, and capped well below the column width — these illustrate
   the prose rather than replace it.  Every one is a 3000px capture shown at
   about 640, so it is also a link to itself at full size. */
.bl-shot {display:block; width:max-content; max-width:100%; margin:.6rem auto .2rem;
          position:relative; border-radius:10px; overflow:hidden;
          border:1px solid var(--bl-border); line-height:0;
          text-decoration:none !important; cursor:zoom-in;}
.bl-shot img {display:block; width:40rem; max-width:100%; height:auto;}
.bl-shot-zoom {position:absolute; right:.5rem; bottom:.5rem;
               padding:3px 10px; border-radius:999px;
               background:rgba(15,23,42,.78); color:#f8fafc;
               font-size:.72rem; font-weight:600; line-height:1.5;
               opacity:0; transition:opacity .15s ease;}
.bl-shot:hover .bl-shot-zoom {opacity:1;}
.bl-shot:hover {border-color:var(--bl-accent-line);}
.bl-shot-cap {text-align:center; font-size:.82rem; color:var(--bl-fg-faint);
              margin:0 0 1.4rem;}

/* The overlay. Opened by :target -- the anchor above sets the fragment -- and
   closed by any other fragment, so it needs no script; Streamlit strips those
   out of markdown anyway.

   The stacking order has to clear Streamlit's own chrome, which sits at 999990
   for the header and 999991 for the sidebar; below those, the overlay covered
   the page but left the header on top of it and its own close button
   unclickable. */
.bl-lightbox {display:none;}
.bl-lightbox:target {display:block; position:fixed; inset:0; z-index:1000000;
                     background:rgba(15,23,42,.92); overflow:auto;
                     overscroll-behavior:contain;   /* don't scroll the page underneath */
                     padding:3.4rem 1rem 2rem; line-height:0;}
.bl-lightbox-bg {position:fixed; inset:0; z-index:0; cursor:zoom-out;}

/* Shown at the width it was *captured* at, not at its pixel width.  These are
   deviceScaleFactor=2 captures of a 1500px-wide viewport (see
   ui/shoot_screenshots.py), so a 3000px file is a 1500px picture at 2x --
   displaying it at 3000 was showing the app at 200% zoom, which is why opening
   one landed you inside a wall of letterforms.  1500 is therefore 1:1, sharp on
   a 2x display, and still more than twice the 40rem thumbnail.

   Capped at the window as well, so the overlay never scrolls sideways: a page
   capture scrolls down, the way the page it is a picture of does.

   The cap sits on the link rather than on the image inside it, so that the auto
   margins have a box the picture's own size to centre.  On max-content the link
   was as wide as the window and the image sat against its left edge. */
.bl-lightbox-fig {display:block; position:relative; z-index:1;
                  width:min(1500px, 100%); margin:0 auto; cursor:zoom-out;}
.bl-lightbox img {display:block; width:100%; height:auto; border-radius:6px;}

/* The bar. Closing used to be a 2.5rem x on top of a screenshot, which is a
   target you have to find before you can leave -- and the pointer over the
   image promised zoom-out while the image itself was inert, because the only
   click-to-close surface was the backdrop and the image covered every pixel of
   it.  Now the image is a close link too, the bar says so, and it stays fixed
   over its own scrim so it is legible whatever it is sitting on.

   "Open the original" is the way *up* in scale rather than another step here:
   at 1:1 the browser's own image viewer has real zoom, which a :target overlay
   cannot, and a tab of its own has a Back button. */
.bl-lightbox-bar {position:fixed; top:0; left:0; right:0; z-index:2;
                  display:flex; align-items:center; gap:.5rem; flex-wrap:wrap;
                  padding:.5rem .9rem; line-height:1.5;
                  background:linear-gradient(rgba(15,23,42,.96), rgba(15,23,42,.72));}
.bl-lightbox-hint {margin-right:auto; color:#cbd5e1; font-size:.78rem;}
.bl-lightbox-act {padding:.3rem .8rem; border-radius:999px;
                  background:rgba(248,250,252,.14); color:#f8fafc !important;
                  font-size:.78rem; font-weight:600; white-space:nowrap;
                  text-decoration:none !important;}
.bl-lightbox-act:hover {background:rgba(248,250,252,.3);}
.bl-lightbox-close {background:rgba(248,250,252,.24);}
/* On a narrow window the sentence wraps the bar onto a second row, and the bar
   is what the overlay's top padding is sized to clear.  The two buttons say
   enough on their own, and tapping the picture is the obvious gesture there. */
@media (max-width:34rem) {.bl-lightbox-hint {display:none;}}
</style>
"""


# ── Rendering a repository markdown file as a page ───────────────────────────
# A markdown image on a line of its own.
_MD_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", re.M)
# A link to a file in the repository. It resolves on GitHub but not in the app,
# where it is shown as the path it points at.
_MD_REL_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://|#)([^)]+)\)")
# A section heading, optionally "N · Title".
_MD_HEADING = re.compile(r"^(#{2,3})[ \t]+(.+?)[ \t]*$", re.M)
# What the app leaves out. The block between these markers is written for someone
# reading TUTORIAL.md on GitHub before they have the app; inside the app it is
# instructions for something already done.
_MD_SKIP = re.compile(r"[ \t]*<!--[ \t]*app:skip-start[ \t]*-->.*?"
                      r"<!--[ \t]*app:skip-end[ \t]*-->[ \t]*\n?", re.S)


def _slug(text: str) -> str:
    """The anchor Streamlit (and GitHub) give a heading of this text.

    Lower-cased, punctuation dropped, runs of anything else collapsed to single
    hyphens — "1 · First run, with the example papers" becomes
    "1-first-run-with-the-example-papers".  Reimplemented rather than guessed at:
    the ids it produces are what both the in-app links and TUTORIAL.md's own
    cross-references resolve against.
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@st.cache_data(show_spinner=False)
def _tutorial_sections():
    """``[(level, number, title, anchor)]`` for every heading the app renders.

    Read from the file rather than listed here, so renaming or renumbering a
    section cannot leave the Tutorial's contents, or the ⓘ links that point into
    it from the flow, aimed at a heading that no longer exists.

    The block the app skips is stripped first, for the same reason: its headings
    are real on GitHub but are not on the page here, and both callers build links
    that have to land somewhere.
    """
    try:
        text = _MD_SKIP.sub("", _TUTORIAL_MD.read_text(encoding="utf-8"))
    except OSError:
        return []
    out = []
    for hashes, title in re.findall(r"^(#{2,3})[ \t]+(.+?)[ \t]*$", text, re.M):
        num, _, rest = title.partition(" · ")
        numbered = num.isdigit()
        out.append((len(hashes), num if numbered else "",
                    rest if numbered else title, _slug(title)))
    return out


def tutorial_href(phrase: str) -> str:
    """Link to the Tutorial section whose title contains *phrase*.

    Matched on a phrase rather than on a slug so that the caller does not have to
    know the section's number — the numbers move when the walkthrough is
    reordered, and a stale ``#4-...`` would land silently on the wrong section.
    Falls back to the top of the page when nothing matches.
    """
    want = phrase.lower()
    for _level, _num, title, anchor in _tutorial_sections():
        if want in title.lower():
            return f"/tutorial#{anchor}"
    return "/tutorial"


def _check_css_comments():
    """Fail loudly on an unbalanced ``/* */`` in any stylesheet above.

    A stray ``*/`` does not raise anything — the browser silently discards the
    rules that follow it, and the page comes back looking like a stylesheet that
    was never written.  It has happened twice to the block comments in these
    literals, each time collapsing Step 1's comparison table without a word in
    any log, so the app refuses to start on one instead.
    """
    for name, css in (("_TOKENS", _TOKENS), ("_CHROME_CSS", _CHROME_CSS),
                      ("_PAGE_CSS", _PAGE_CSS), ("_TUTORIAL_CSS", _TUTORIAL_CSS),
                      ("_ROW_TINT_CSS", _ROW_TINT_CSS)):
        depth = 0
        for token in re.findall(r"/\*|\*/", css):
            depth += 1 if token == "/*" else -1
            if depth < 0 or depth > 1:
                raise AssertionError(f"{name}: unbalanced CSS comment ({token})")
        if depth:
            raise AssertionError(f"{name}: unterminated CSS comment")


_check_css_comments()


def step_header(num, title, hint="", state="", tone="idle", into=None, learn=""):
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

    *learn* is the phrase naming the Tutorial section that explains this step.
    It becomes an ⓘ beside the title, linking straight to that heading — the
    walkthrough is on another page, and without a way in from here it only helps
    the people who thought to go and read it first.  The link opens in a tab of
    its own (a named one, so repeated clicks reuse it rather than pile up):
    following it in place would reload the app and lose the loaded papers.
    """
    st_ = into if into is not None else st.container(key=f"blstep{num}")
    href = tutorial_href(learn) if learn else ""
    info = (f"<a class='bl-info' href='{href}' target='bl-tutorial' "
            f"title='Read about this step in the Tutorial'>&#9432;</a>") if href else ""
    st_.markdown(
        f"<div class='bl-step'><span class='bl-step-n'>{num}</span>"
        f"<span class='bl-step-t'>{title}</span>{info}"
        + (f"<span class='bl-step-h'>{hint}</span>" if hint else "")
        + (f"<span class='bl-step-s bl-{tone}'>{state}</span>" if state else "")
        + "</div>", unsafe_allow_html=True)


def _options_state(checker):
    """Step 3's status pill: the options in force, named before they render.

    Read out of ``st.session_state`` rather than from the widgets, because the
    header is drawn above the expander that holds them.  Streamlit has already
    written this rerun's values there, so the pill is never a run behind.
    """
    bits = []
    if checker.needs_llm:
        model = selected_model()
        bits.append(f"{selected_provider()} / {model}" if model else selected_provider())
    bits.append("Crossref on" if st.session_state.get("cr", True) else "Crossref off")
    if checker.kind != "csv":                 # batch mode ignores both lists
        bits += [name for name, key in (("whitelist", "wl"), ("blacklist", "bl"))
                 if (st.session_state.get(key) or "").strip()]
    return " · ".join(bits)


def _llm_env(checker, provider, model):
    """The environment overrides Step 3's LLM choices amount to, or ``None``.

    Nothing at all for the checks that use no LLM: they would ignore both names,
    and setting them anyway would put a choice the user did not make into the
    environment of a run that cannot act on it.
    """
    if not checker.needs_llm:
        return None
    env = {"LLM_PROVIDER": provider} if provider else {}
    if model:
        env["AZURE_OPENAI_DEPLOYMENT"] = model
    return env or None


def _provider_changed():
    """Move the chosen model to the new provider's list (an ``on_change``).

    The two are not independent: a deployment name from one backend rarely means
    anything to another, so the model cannot simply be left as it was.

    It is *reassigned* rather than deleted, which matters.  Deleting it left the
    picker offering the new provider's names while still displaying the old
    provider's — Streamlit rebuilds the option list, but a widget whose stored
    value has vanished is not re-rendered with its new default, and the label a
    dropdown shows is the one thing about it a user has to be able to trust.
    Writing a value the new list actually contains settles it.

    Callbacks run before the rerun that redraws the widgets, so what is written
    here is what they come back holding.
    """
    st.session_state.pop("model_custom", None)
    _ok, _msg, models = llm_status(selected_provider())
    st.session_state["model"] = models[0] if models else _OTHER_MODEL


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
    # Step 3's provider starts at `.env`'s, not at whichever happens to head the
    # list.  Set here rather than there because Step 1's readiness line, drawn
    # first, has to be answering about the same one.
    st.session_state.setdefault("provider", ENV_PROVIDER)

    # Step 1 — choose a check.  A comparison table rather than a dropdown: the
    # three are meant to be weighed against each other, and a dropdown shows only
    # the option already chosen.
    step_header(1, "Choose a check", "what to run over your papers",
                f"{checker.icon} {checker.title}", "ok", learn="First run")
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
    llm_html, llm_models = "&nbsp;", []
    if checker.needs_llm:
        llm_slot.markdown("<div class='bl-llm'>⏳ Checking the LLM configuration…</div>",
                          unsafe_allow_html=True)
        # Asked about the provider Step 3 has selected, not about `.env`'s: the
        # two are the same until a run is pointed elsewhere, and after that a
        # line answering for `.env` would be answering about a different run.
        llm_ok, llm_msg, llm_models = llm_status(selected_provider())
        # Step 3's picker would default to the first configured name anyway, but
        # not until it has drawn — one rerun after this line and the status pill
        # have already had to say which model the run would use.  Written down
        # here instead, so all three read one value rather than each falling back
        # to its own guess.
        if llm_models:
            st.session_state.setdefault("model", llm_models[0])
        if llm_ok:
            # Likewise the model: the one Step 3 has selected, not simply the
            # first configured, so this line and the run cannot disagree.
            chosen = selected_model()
            llm_html = (f"🔑 LLM ready — <code>{html.escape(selected_provider())}</code> / "
                        + (f"<code>{html.escape(chosen)}</code>." if chosen
                           else "<b>no model chosen yet</b> — name one in Step 3."))
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
                "ok" if items else "wait", into=head2, learn="your own papers")
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
    step_header(3, "Options", "all optional", _options_state(checker),
                learn="venue lists")

    # Which LLM answers is a run setting, so it is decided here rather than only
    # reported in the sidebar.  Editing .env and restarting the app was the
    # previous way to change either half of it, which is a lot of ceremony for a
    # decision taken per run — and it made trying a second provider a
    # configuration exercise rather than a choice.
    #
    # Both halves are offered, because either alone is only half an answer: a
    # model name means nothing without the backend that serves it, and picking a
    # provider without saying which of its models to use just moves the question.
    model = provider = ""
    if checker.needs_llm:
        provider_col, model_col = st.columns(2)
        provider = provider_col.selectbox(
            "LLM provider", llm_providers(), key="provider", on_change=_provider_changed,
            help="Which backend answers. Defaults to `.env`'s `LLM_PROVIDER`; choosing "
                 "another here changes this run only, and leaves `.env` alone. Each "
                 "provider needs its own credentials — the line under Step 1 says whether "
                 "the one selected has them.")
        # The configured names, and a way past them.  A list alone was a dead end
        # on any machine whose .env happened not to name the wanted model: the
        # advice was to edit .env and restart, which is exactly the ceremony this
        # control exists to remove.  A typed name cannot be checked before the
        # run — see the caption — but "cannot be verified" is not "must not be
        # offered", and an unverified name that runs beats a verified one that
        # is not the model the user meant.
        model = model_col.selectbox(
            "Model / deployment", llm_models + [_OTHER_MODEL], key="model",
            help="Which model of that provider this run uses. The listed names come from "
                 "`.env` — `AZURE_OPENAI_DEPLOYMENT` for the provider `.env` itself "
                 "selects, `LLM_MODELS_<PROVIDER>` for any other — each of which takes a "
                 "bare name, a comma-separated list or a JSON array. Not listed is not "
                 "unavailable: choose *Other* and type any name the provider serves.")
        if model == _OTHER_MODEL:
            model = st.text_input(
                "Model name", key="model_custom", placeholder="e.g. gpt-4o-mini",
                help="Passed to the provider exactly as typed. For Azure this is the "
                     "**deployment** name, which need not match the underlying model's.").strip()
            st.caption("Typed names are not checked before the run: whether the provider "
                       "serves this one is answered by the provider, on the first request. "
                       "Add it to `.env` to have it offered in the list from now on.")

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
    # Not shown at all for Batch CSV, which takes neither list — batch_check.py
    # has no --whitelist or --blacklist to give them to.  It used to be shown
    # anyway, holding a note that said so, which made "Advanced — narrow the run,
    # or add venues of your own" a control that promised two things and then
    # apologised for having neither.  Step 4's summary already reports the same
    # fact as a property of the run rather than as a dead end, which is where it
    # belongs and where it does not cost a click to find.
    wl = bl = None
    if checker.kind != "csv":
        with st.expander("🎯 Advanced — narrow the run, or add venues of your own"):
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
                "ready" if items else "add papers first", "ok" if items else "wait",
                learn="Understanding the report")
    lists_na = checker.kind == "csv"
    # Batch CSV's two "not used here" cells carry the reason as hover text. They
    # are the only place the flow still mentions the lists for that check, now
    # that Step 3 does not offer an expander it cannot fill.
    na_why = (f"{checker.title} classifies every record it is given, so it has no scope "
              "filter and no way to extend the list — batch_check.py takes neither. "
              "Both are available on the fast and thorough checks.")
    summary = [
        ("Check", f"{checker.icon} {checker.title}", "", ""),
        ("Papers", f"{len(items)} loaded" if items else "none yet",
         "" if items else "bl-sum-todo", ""),
        ("Crossref", "On" if crossref else "Off", "" if crossref else "bl-sum-off", ""),
        ("Whitelist", "not used here" if lists_na else
         (f"{len(wl)} venue(s)" if wl else "—"),
         "bl-sum-off" if lists_na or not wl else "", na_why if lists_na else ""),
        ("Blacklist", "not used here" if lists_na else
         (f"{len(bl)} venue(s)" if bl else "—"),
         "bl-sum-off" if lists_na or not bl else "", na_why if lists_na else ""),
        ("You get back", checker.output_label, "", checker.output_note),
    ]
    if checker.needs_llm:
        # Only the check that spends tokens has an LLM, so only it is asked
        # about — and it is asked as one thing, because provider and model are
        # only an answer together.
        summary.insert(1, ("LLM", f"{provider} / {model}" if model
                           else f"{provider} / no model chosen",
                           "" if model else "bl-sum-todo",
                           "" if model else "Name a model in Step 3. Without one the run "
                                            "falls back to whatever .env configures."))

    def _cell(k, v, cls, why):
        attrs = (f" class='bl-sum-v {cls} bl-help' title='{html.escape(why, quote=True)}'"
                 if why else f" class='bl-sum-v {cls}'")
        return f"<div><span class='bl-sum-k'>{k}</span><span{attrs}>{v}</span></div>"

    # Keyed so that the screenshot script can wait on this summary — the one place
    # that states which check a press of Run would actually start.
    with st.container(key="runsummary"):
        st.markdown("<div class='bl-sum'>"
                    + "".join(_cell(*row) for row in summary)
                    + "</div>", unsafe_allow_html=True)
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
                    # The choices reach the check the way the check already looks
                    # for them: openai_client reads LLM_PROVIDER and
                    # AZURE_OPENAI_DEPLOYMENT from the environment, so the
                    # subprocess is simply started with those two set. An
                    # environment variable beats the .env file, which python-dotenv
                    # does not use to overwrite one, so no other file is touched
                    # and nothing outlives the run. A model left unchosen is left
                    # unset rather than sent as an empty name.
                    res = runners.run_checker(
                        checker, items, aux_lists=aux_lists, extra_args=extra_args,
                        env_overrides=_llm_env(checker, provider, model),
                        on_line=on_line)
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


def _shot_html(src: str, alt: str, ident: str) -> str:
    """A screenshot, and the full-size copy a click on it opens.

    Not ``st.image``: that re-encodes a PNG as JPEG — visibly lossy on a picture
    of text, which every one of these is — and offers no way through to the
    original.  Served instead from ``ui/static`` (see .streamlit/config.toml) as
    the PNG it is.

    The overlay is opened by the anchor's fragment and closed by another, so no
    script is involved: Streamlit strips ``<script>`` out of markdown, and this
    needs none.  That is also why every way out has to be a link, and why there
    are three of them — the image, the backdrop around it, and the bar's own
    button.  Closing was previously that button alone, hunted for on top of a
    screenshot that had covered the window, and the image beneath the pointer
    said zoom-out while doing nothing.

    A link to the file itself is offered rather than a second magnification.  At
    1:1 the browser's image viewer zooms both ways and its tab has a Back
    button, neither of which a stylesheet can provide.
    """
    alt_a = html.escape(alt, quote=True)
    return (
        f"<a class='bl-shot' href='#{ident}' title='Click to see it full size'>"
        f"<img src='{src}' alt='{alt_a}'>"
        f"<span class='bl-shot-zoom'>Click to enlarge</span></a>"
        + (f"<div class='bl-shot-cap'>{html.escape(alt)}</div>" if alt else "")
        + f"<div class='bl-lightbox' id='{ident}'>"
          f"<a class='bl-lightbox-bg' href='#bl-closed' tabindex='-1' aria-hidden='true'></a>"
          f"<div class='bl-lightbox-bar'>"
          f"<span class='bl-lightbox-hint'>Click the image — or anywhere around it — "
          f"to close</span>"
          f"<a class='bl-lightbox-act' href='{src}' target='_blank' rel='noopener'"
          f" title='The file itself, at full resolution, in a tab of its own'>"
          f"Open the original &#8599;</a>"
          f"<a class='bl-lightbox-act bl-lightbox-close' href='#bl-closed'>&times; Close</a>"
          f"</div>"
          f"<a class='bl-lightbox-fig' href='#bl-closed' aria-label='Close'>"
          f"<img src='{src}' alt='{alt_a}'></a></div>")


def _heading_html(hashes: str, title: str) -> str:
    """One section heading, as its own HTML so it can carry a numbered badge.

    The numbers are the walkthrough's spine, and as plain markdown they were just
    the first characters of a line of prose.  Rendered here the way the flow's own
    steps are — a filled circle, then the title — so the two pages read as one
    piece of software.

    The ``id`` is the slug Streamlit would have generated, so the links into these
    sections keep working and so ``TUTORIAL.md``'s own cross-references resolve to
    the same anchors on GitHub.
    """
    tag = "h2" if len(hashes) == 2 else "h3"
    anchor = _slug(title)
    num, _, rest = title.partition(" · ")
    if not num.isdigit():
        return (f"<{tag} id='{anchor}' class='bl-h bl-h-plain'>"
                f"{html.escape(title)}</{tag}>")
    return (f"<{tag} id='{anchor}' class='bl-h'>"
            f"<span class='bl-h-n'>{html.escape(num)}</span>"
            f"<span>{html.escape(rest)}</span></{tag}>")


def _contents_html() -> str:
    """The page's sections, as a menu at the top.

    A row per section rather than a strip of chips: a contents list is
    navigation, and navigation is read down a column, not across a wrapped line.
    The rows also give each entry a full-width target and a stable position — the
    chips re-wrapped at every window size — and room for the title in full rather
    than however much of it fits in a pill.

    **Every** level-2 section, numbered or not. "What this tool checks" and
    "Where to go next" are as jumpable as the seven steps between them, and a
    contents list that quietly omitted them would be misdescribing the page. They
    carry a dot in the number's place, so the column still lines up.
    """
    items = [(num, title, anchor) for level, num, title, anchor in _tutorial_sections()
             if level == 2]
    if not items:
        return ""
    rows = "".join(
        f"<a class='bl-toc-row' href='#{anchor}'>"
        f"<span class='bl-toc-n{'' if num else ' bl-toc-n-plain'}'>{num or '&bull;'}</span>"
        f"<span class='bl-toc-t'>{html.escape(title)}</span></a>"
        for num, title, anchor in items)
    return f"<div class='bl-toc'><div class='bl-toc-k'>Contents</div>{rows}</div>"


def _render_markdown_file(path, *, skip_app_only=False, static_url="", before_sections=""):
    """Render a repository markdown file as a page, images and headings and all.

    *skip_app_only* drops the block the file fences off as not worth showing in
    the app.  *static_url* is the prefix under which the file's images are served,
    which is what lets them be plain ``<img>`` tags.  *before_sections* is HTML to
    place immediately above the first section heading — after the title and the
    file's opening note, which is where a table of contents belongs.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        st.error(f"Could not read `{path.name}`: {exc}")
        return
    if skip_app_only:
        text = _MD_SKIP.sub("", text)
    if before_sections:
        first = _MD_HEADING.search(text)
        if first:
            text = text[:first.start()] + before_sections + "\n\n" + text[first.start():]

    def chunk(md):
        md = md.strip()
        if md:
            md = _MD_HEADING.sub(lambda m: _heading_html(m.group(1), m.group(2)), md)
            st.markdown(_MD_REL_LINK.sub(r"\1 (`\2`)", md), unsafe_allow_html=True)

    pos = 0
    for i, m in enumerate(_MD_IMAGE.finditer(text)):
        chunk(text[pos:m.start()])
        name = Path(m.group(2)).name
        if static_url and (path.parent / m.group(2)).is_file():
            st.markdown(_shot_html(static_url + name, m.group(1), f"bl-shot-{i}"),
                        unsafe_allow_html=True)
        pos = m.end()
    chunk(text[pos:])


def page_tutorial():
    """The walkthrough, rendered from ui/TUTORIAL.md so there is one copy of it.

    Rendered into a keyed container so the stylesheet can hold the text to a
    readable measure, and with the file's install-and-launch block dropped: the
    app is demonstrably running by the time anyone reads this page, so telling
    them how to start it is the one section that cannot apply.  It stays in the
    file for the GitHub reader, who has not started it yet.
    """
    st.markdown(_TUTORIAL_CSS, unsafe_allow_html=True)
    with st.container(key="tutorial"):
        _render_markdown_file(_TUTORIAL_MD, skip_app_only=True,
                              static_url="/app/static/",
                              before_sections=_contents_html())


# ── Navigation ──────────────────────────────────────────────────────────────
# Two pages in the sidebar: the flow, and the instructions for it.
nav = st.navigation([
    # The default page is served from "/", so it is given no url_path of its own.
    st.Page(page_check, title="Run a check", icon="▶️", default=True),
    st.Page(page_tutorial, title="Tutorial", icon="📘", url_path="tutorial"),
])
sidebar()          # below the page links, on every page
nav.run()
