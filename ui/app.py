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
import time
from pathlib import Path

import openpyxl
import streamlit as st
from dotenv import load_dotenv

import crossref
import doaj
import runners
import styles
import venue_lookup

# Step 3's venue boxes search DOAJ while a name is being typed, which Streamlit's
# own widgets cannot do — they filter options client-side and call back only on
# Enter or blur.  Optional at import: a checkout whose venv predates this
# dependency still runs, with the boxes falling back to plain text entry and the
# lookup simply absent.  requirements.txt and both run_ui launchers install it.
try:
    from streamlit_searchbox import st_searchbox
except Exception:
    st_searchbox = None

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


def notify_next_run(msg, icon=None):
    """Queue a popup for the render that is about to start, not this one.

    ``_add_pick`` reruns the moment it has added a venue — it has to, because the
    box only empties when the component is remounted — and everything this run
    had drawn, a toast included, is abandoned with it.  Queued here and emitted
    by :func:`flush_notices` at the top of the next render instead, so "Added X"
    survives the rerun that makes it true.
    """
    st.session_state.setdefault("_notices", []).append((msg, icon))


def flush_notices():
    """Show anything :func:`notify_next_run` left for this render."""
    for msg, icon in st.session_state.pop("_notices", []):
        notify(msg, icon)


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
    """Sidebar content, below the page links: what this is, and where to learn it.

    Kept short on purpose.  The sidebar is on every page, so it holds only what
    is true on every page: what the tool answers and where the instructions are.
    What used to be here besides — how the three checks differ, what shape the
    input takes — now lives where it is acted on, in Step 1's comparison table
    and Step 2's caption, rather than being said twice.

    An **Environment** panel used to sit here too, naming the provider and model
    ``.env`` configures.  It is gone, and its absence is the point: the LLM is
    used by one of the three checks, so a panel on every page of every run was
    answering a question most runs do not ask.  Everything it said now sits where
    it is acted on — the two values are the starting positions of Step 3's own
    pickers, and whether they *work* is reported by Step 1's readiness line and
    again in Step 4's summary, beside the rest of what is about to run.
    """
    st.sidebar.markdown("### Beall's List check")
    st.sidebar.caption(
        "Is a paper's **journal or publisher** on Beall's List of *potentially* "
        "predatory venues? A match means the **venue appears on the list** — not "
        "that the paper is bad.")
    st.sidebar.caption("📘 New here? Open the **Tutorial** page above.")


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


# ── Step 3's venue lists ─────────────────────────────────────────────────────
# A venue list is a *list*, and it is now edited as one: rows with a ✕, built by
# searching DOAJ or by typing, with the JSON it serialises to kept underneath in
# a fold.  It used to be a 90px textarea holding raw JSON, which made the format
# the interface — every add was text surgery, every removal was careful text
# surgery, and after half a dozen entries the box was a wall of braces that no
# longer answered the only question it is there to answer, which is *which
# venues is this run scoped to*.
#
# The JSON has not gone anywhere and is not demoted to a preview: it is still a
# real editor, it still accepts a pasted list from a colleague or a previous run,
# and it stays in sync both ways.  It is simply no longer the only way in.
#
# Two identifiers per entry rather than one, because a venue can be named three
# ways and they are not equally good:
#   * an ISSN names one journal and nothing else;
#   * a domain is matched by host, so a publisher's domain covers every journal
#     it hosts — deliberately useful, accidentally enormous;
#   * a name is matched as a whole-word phrase, so a short one ("Sensors") is
#     shared by journals that have nothing to do with each other.
# Each row therefore says which of the three it carries, because that — not the
# venue's name — is what decides which papers the run will look at.

# Rows are Streamlit widgets, and a pasted list of 200 venues would be 400 of
# them.  Past this many the list stops being rendered row-by-row and says so;
# every entry is still in force, and the fold below holds all of them.
_MAX_SHOWN_VENUES = 25


# How many matches the menu opens with, and how many more each Show more adds.
# The wanted venue is normally in the first few, so this is a shortlist rather
# than a results page — but it says how many it is holding back, and the last row
# of the menu fetches them, so the shortlist never passes for the whole answer.
_LOOKUP_LIMIT = 8
_LOOKUP_MORE = 20

# The value behind that last row.  A plain string rather than a sentinel object
# because it travels through the component's own option state.
_SHOW_MORE = "__bl_show_more__"

# The two directories, and why each list searches the one it does.
#
# DOAJ lists journals that PASSED an editorial screening, which is the right
# catalogue for naming the venues a run should be narrowed to — and the wrong one
# for a blacklist, where the venue being named is by definition not in a
# directory of vetted ones.  Crossref registers a DOI for whoever pays to be a
# member and screens nobody, so it holds the venues a blacklist is for: paying to
# register DOIs is part of looking established.
#
# The debounce differs because the cost of a keystroke does.  DOAJ is a local
# index — the whole list is downloaded once — so a lookup is a dictionary scan
# and there is no reason to wait.  Crossref is answered over the network in about
# half a second and rate-limited per second (see crossref.py), so its keystrokes
# are coalesced and it does not start until there are two letters to send.
_DIRECTORIES = {
    "doaj": {"name": "DOAJ", "debounce": 120},
    "crossref": {"name": "Crossref", "debounce": 300},
}

# What the same box costs when the directory is a file on this machine instead of
# a service: no round trip, so no reason to coalesce keystrokes as hard and no
# reason to hold the first letters back.  DOAJ has always been read this way;
# Crossref is read this way only once the Settings page has fetched it.
_LOCAL_DEBOUNCE = 120


def directory_of(source):
    """The directory a list searches, as it is *right now*.

    The name is fixed; how the box behaves is not, because Crossref can be either
    a live API or a local copy and the two do not deserve the same debounce.  Read
    per render, so downloading or removing the copy on the Settings page changes
    the box on the next interaction without anything having to be told.
    """
    directory = _DIRECTORIES.get(source)
    if not directory:
        return None
    local = source == "doaj" or (source == "crossref" and crossref.have_local())
    return {**directory,
            "local": local,
            "debounce": _LOCAL_DEBOUNCE if local else directory["debounce"]}

# What the last lookup found, as a mapping rather than a positional tuple: it is
# written from four places and read in one, and a tuple that grew a field once
# already broke the reader from a site that had not been updated with it.
_LOOKUP_IDLE = {"term": "", "shown": 0, "total": 0, "error": "", "pending": False}


@st.cache_resource(show_spinner=False)
def doaj_index():
    """DOAJ's whole journal list, read from the local copy and kept for the process.

    ``cache_resource`` rather than ``cache_data``: this is one large object that
    every session should share, not a per-call value worth copying.

    It **never downloads**.  ``load_journals`` reads the disk cache and, finding
    none, would fetch 25 MB from inside a keystroke — which used to be exactly
    what happened the first time anyone used the whitelist box.  Both directories
    are now downloaded only where a download is asked for, on the Settings page,
    so the first run of this app costs nothing it did not ask for.

    Returns ``(index, error)``; *error* is shown under the box and is empty on
    success.
    """
    journals, error = doaj.load_journals()
    return doaj.build_index(journals), error


@st.cache_resource(show_spinner=False)
def crossref_index():
    """Crossref's whole journal list, when there is a local copy of it.

    The blacklist works without this — ``crossref.search`` asks the live API, which
    needs no download — so unlike :func:`doaj_index` this is only ever called when
    the Settings page has already fetched the file.  It never fetches one itself:
    ``load_journals`` reads the disk cache and, finding none, would start a 51 MB
    download from inside a keystroke.  That is a thing to offer, not to do.

    Returns ``(index, error)``.
    """
    journals, error = crossref.load_journals()
    return crossref.build_index(journals), error


def clear_directory_caches():
    """Forget both in-memory indexes, after either file has changed on disk.

    They are ``cache_resource``, so they outlive the run that built them — which
    is the point, and also means a download or a delete would otherwise leave the
    boxes searching the list from before it.
    """
    for cached in (doaj_index, crossref_index):
        try:
            cached.clear()
        except Exception:
            pass


def searchbox_styles(*, has_matches=True):
    """Colours for the search menu, in the theme that is actually on screen.

    *has_matches* says whether the component is holding anything to list.  When
    it is not, the whole component is clipped to the field so that no menu can
    show below it — see **An empty box must not open an empty menu** below.

    The menu is drawn by react-select inside the component's iframe, where this
    file's stylesheet cannot reach it, so everything it needs has to be passed in
    as values — and a value has to name one theme.

    **What was wrong, and why neither a background nor a hairline fixed it.**  The
    component fills its menu with Streamlit's *page* background, so the open
    dropdown was drawn in the exact colour of the page it was covering.  It sizes
    itself to its contents, so a two-line match covers the note and the top of the
    venue list and stops: the list box's border resumes immediately underneath and
    its rows carry on in the same colour, at the same width, in the same kind of
    row.  The two panels merge into one, and the list's entries read as more
    matches — "the text behind the dropdown, showing on the dropdown".  A 1px ring
    was not enough, because the list box below has a 1px border too.

    What separates an overlay from what it covers is **elevation**, and elevation
    is not the same thing in the two themes:

      * **Dark** — a shadow has almost nothing to darken against slate-900, so the
        lift has to be a *lighter surface*: the menu and the field sit on
        slate-800, one step above the page, the way every dark UI marks a raised
        plane.  That is what makes the dropdown a different object from the page,
        rather than a bordered region of it.
      * **Light** — white on white needs the opposite: the surface stays white and
        the depth comes from a real shadow, which does show on a light page.

    Both also get a ring a shade stronger than the venue list's border, so where
    the two panels do meet the edge belongs to the one in front.

    Three mechanics decide how that is written:

      * The ring is an ``inset`` box-shadow rather than a ``border``.  A border
        would change the list's box, and the list has to stay exactly the size of
        the container react-select draws behind it — see the radius note below.
      * ``borderRadius`` is **4px, matching react-select's own menu**.  The
        component forwards only the keys it documents and `menu` is not one of
        them, so that container keeps react-select's white `neutral0`; the list
        sits in the same box and hides it.  Round the list *more* than 4px and
        the white reappears at all four corners — measured, and the reason this
        does not simply use the app's 8px.
      * Naming an option's ``backgroundColor`` **flattens its hover**: these
        overrides are spread over a value the component computes from the row's
        state, so the row under the cursor stops changing colour.  A raised menu
        has to name one, so the hover is given back as a nested ``:hover`` — the
        styles reach react-select through emotion, which honours it.

    **An empty box must not open an empty menu.**  The component decides that for
    itself, with ``menuIsOpen: this.props.args.options && this.state.menu`` — and
    ``state.menu`` is true from the moment the box has focus, so the whole guard
    is ``args.options``, which is an **empty JavaScript array**, which is
    **truthy**.  Clicking an untouched box therefore opened a menu holding
    nothing.  That was invisible while the menu was the page's own colour; a
    raised plane turned it into a small empty panel appearing under an empty
    field.

    Nothing can be done from the options side: making them falsy would close the
    menu, but ``openMenu`` maps over them *outside* that guard, so a falsy value
    throws (measured — ``Cannot read properties of null (reading 'map')`` on the
    first click).  No JavaScript value is both falsy and mappable.  So the menu
    is left to open and is **clipped away instead**: ``overflow: hidden`` on the
    component's own wrapper, which is an ancestor of the box the menu is
    positioned against, and only for as long as there is nothing to show.  It
    also keeps the frame at the height of the field, since a clipped menu adds
    no scrollable overflow for Streamlit to size the iframe to.

    That clip is why the focus ring is drawn ``inset``.  An outset ring would be
    clipped along with the menu, and the field would lose its focus state exactly
    when it is empty — which is when it is most likely to be focused.

    ``st.context.theme.type`` is what makes the palette answerable.  The rest of
    this app's styling is written not to need it — see the note above ``_TOKENS``
    — because CSS cannot ask; but here the question is asked in Python, once per
    render, and it reports the theme *in force* including the user's ⚙ override
    rather than the one `.streamlit/config.toml` starts from.

    Guarded: on a Streamlit without ``st.context.theme`` the menu is styled for
    the app's own default base rather than losing its elevation again.
    """
    dark = styles.theme_is_dark()
    # Named by the job each colour does, not by where it happens to be used: the
    # point of the fix is that `page` and `raised` are *different*.
    raised = "#1e293b" if dark else "#ffffff"       # the open menu — one plane up
    field = styles.FIELD_BG[dark]                         # the box, as Streamlit fills its own
    rowhover = "#334155" if dark else "#f1f5f9"     # slate-700 / slate-100
    text = "#f1f5f9" if dark else "#1e293b"         # slate-100 / slate-800
    edge = styles.FIELD_EDGE[dark]                        # slate-600 / slate-300
    page = "#0f172a" if dark else "#ffffff"         # what the overlay is lying on
    accent = "#3b82f6"                              # blue-500, as everywhere else
    # What the typed letters are marked with inside a match. The app's blue at low
    # opacity rather than a grey block: it says "this is the bit you typed" in the
    # colour the page already uses for "this one", and it works on either surface.
    mark = "rgba(59,130,246,.32)" if dark else "rgba(59,130,246,.20)"
    # Depth for the light theme, where it reads; on dark it is only a settling
    # shadow under a surface that is already doing the lifting.
    lift = ("0 10px 26px rgba(2,6,23,.55)" if dark else
            "0 12px 30px rgba(15,23,42,.18), 0 2px 6px rgba(15,23,42,.10)")
    return {
        "dropdown": {"width": 0, "height": 0, "fill": "transparent"},
        # The clip, applied to the component's outermost element. Only while
        # there is nothing to list — with matches it has to be off, or the menu
        # it is there to suppress would be suppressed too.
        "wrapper": {} if has_matches else {"overflow": "hidden"},
        "searchbox": {
            "menuList": {
                "backgroundColor": raised,
                "borderRadius": "4px",
                # Fixed, not `max`: react-select's own 300px cap would still let
                # the panel be shorter than that, and a panel whose height
                # follows the match count puts its bottom edge inside a row of
                # the list underneath. See styles.MENU_HEIGHT.
                "height": styles.MENU_HEIGHT,
                "maxHeight": styles.MENU_HEIGHT,
                # Three shadows, and the order is the point — the first is painted
                # on top.  The ring, then the lift, then the hard copy of the
                # panel in the page's colour that hides the white band and gives
                # the lift something to fall on.  See styles.MENU_GAP.
                "boxShadow": (f"inset 0 0 0 1px {edge}, {lift}, "
                              f"0 {styles.MENU_GAP}px 0 1px {page}"),
                # 4px keeps the first and last row off the ring; the band is the
                # list's own background, so nothing shows through it.
                "padding": "4px 0",
                "overflow": "auto",
                # The one line that lets the shadow exist at all.  Streamlit sizes
                # the component's iframe to `document.body.scrollHeight`, which
                # ended at the menu's bottom edge — so a drop shadow, which paints
                # *outside* that edge, was clipped away entirely, and the panel met
                # the venue list box with nothing in between.  This margin is
                # counted rather than collapsed away, because a scrolling list is
                # its own formatting context (measured: scrollHeight 118 → 142), so
                # the frame grows past the panel and there is somewhere for the
                # shadow and the gap to be.
                "marginBottom": f"{styles.MENU_GAP}px",
            },
            # A box that looks like a box. The component leaves an unfocused
            # control with `1px transparent`, which on this page is a field with
            # no edge at all — placeholder text floating where a control should
            # be. Its own `&:hover` has to be replaced too, or hovering puts the
            # transparent border back.
            "control": {
                "backgroundColor": field,
                "border": f"1px solid {edge}",
                "boxShadow": "none",
                "&:hover": {"border": f"1px solid {edge}"},
                # Inset, because the wrapper above may be clipping. See the
                # docstring: an outset ring would be cut off on an empty box.
                ":focus-within": {"border": f"1px solid {accent}",
                                  "boxShadow": f"inset 0 0 0 2px rgba(59,130,246,.45)"},
            },
            "singleValue": {"color": text},
            "input": {"color": text},
            "option": {"backgroundColor": raised, "color": text,
                       "highlightColor": mark,
                       ":hover": {"backgroundColor": rowhover},
                       ":active": {"backgroundColor": rowhover}},
            "optionEmpty": "hidden",
        },
    }


@st.cache_data(show_spinner=False, ttl=3600)
def crossref_search(term: str, limit: int):
    """Memoized :func:`crossref.search` — one request per distinct term per hour.

    Caching is what makes a networked directory usable behind a box that searches
    while a name is typed: Streamlit reruns the whole script on every click
    anywhere on the page, and the prefixes of a word are re-sent as it is
    extended and backspaced.  ``crossref.search`` returns its failures rather
    than raising precisely so they land in this cache too — an exception is not
    cacheable, so a refused query would otherwise be re-sent forever.
    """
    return crossref.search(term, limit=limit)


def _venue_key(entry):
    """Identity of an entry, so the same venue cannot be added twice."""
    return (
        venue_lookup.normalize_host(entry.get("domain") or entry.get("url") or ""),
        (entry.get("name") or "").strip().casefold(),
        tuple(sorted(entry.get("issns") or [])),
    )


def _venue_name(entry):
    """What to call an entry: the most human of whatever it carries."""
    return (entry.get("name") or entry.get("domain") or entry.get("url")
            or (entry.get("issns") or [""])[0] or "?")


def _normalize_venue(item):
    """One list item as an entry dict, accepting everything the JSON format does.

    A bare string is a name, ``url`` and ``domain`` are interchangeable, and
    anything with none of the three identifying fields is dropped — the same
    rule ``load_user_entries`` applies when the check reads the file, applied
    here so the rows on screen are the entries that will actually be in force.
    """
    if isinstance(item, str):
        item = {"name": item}
    if not isinstance(item, dict):
        return None
    entry = {}
    if (item.get("name") or "").strip():
        entry["name"] = item["name"].strip()
    site = (item.get("domain") or item.get("url") or "").strip()
    if site:
        entry["domain"] = venue_lookup.normalize_host(site) or site
    issns = [venue_lookup.normalize_issn(i) for i in (item.get("issns") or [])]
    issns = [i for i in issns if i]
    if issns:
        entry["issns"] = issns
    return entry or None


def _venues_to_json(venues):
    return json.dumps(venues, indent=2, ensure_ascii=False) if venues else ""


def _sync_venue_json(key):
    """Write the list back into the JSON box.

    Only ever called from a widget callback, which Streamlit runs *before* the
    rerun that redraws the widgets — so assigning to the text area's own key is
    allowed here and is what it comes back holding.  Doing it from the render
    pass instead would raise.
    """
    st.session_state[key] = _venues_to_json(st.session_state.get(f"{key}_venues") or [])
    st.session_state.pop(f"{key}_jsonerr", None)


def _add_venue(key, entry):
    """Append one entry unless an identical one is already there.

    Both messages are queued rather than shown, because one of the two callers
    (:func:`_add_pick`) reruns immediately afterwards and would take a toast down
    with it.  The other caller loses nothing by it: a queued notice appears on
    the render that follows a callback, which is the same moment.
    """
    venues = st.session_state.setdefault(f"{key}_venues", [])
    if any(_venue_key(v) == _venue_key(entry) for v in venues):
        notify_next_run(f"{_venue_name(entry)} is already on the list.", icon="•")
        return
    venues.append(entry)
    _sync_venue_json(key)
    notify_next_run(f"Added {_venue_name(entry)}.", icon="✅")


def _remove_venue(key, index):
    venues = st.session_state.get(f"{key}_venues") or []
    if 0 <= index < len(venues):
        venues.pop(index)
        _sync_venue_json(key)


def _clear_venues(key):
    st.session_state[f"{key}_venues"] = []
    _sync_venue_json(key)


def _entry_from_text(text):
    """Read one typed venue as an ISSN, a website or a name.

    One box rather than three, because the three are alternatives and asking for
    all of them would imply they are wanted together.  Which one was typed is
    decided here and shown on the row that appears, so a misread is visible
    immediately rather than at the end of the run.
    """
    text = (text or "").strip()
    if not text:
        return None
    issn = venue_lookup.normalize_issn(text)
    if issn and not text.replace("-", "").strip().isalpha():
        return {"issns": [issn]}
    if " " not in text and "." in text and venue_lookup.normalize_host(text):
        return {"domain": venue_lookup.normalize_host(text)}
    return {"name": text}


def _venue_json_edited(key):
    """Parse the JSON box back into the list (its ``on_change``).

    Invalid JSON does not clear the list: the last list that parsed stays in
    force, and the box says so.  Silently emptying the run's scope because a
    comma went missing is the one behaviour this must not have.
    """
    raw = (st.session_state.get(key) or "").strip()
    if not raw:
        st.session_state[f"{key}_venues"] = []
        st.session_state.pop(f"{key}_jsonerr", None)
        return
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        st.session_state[f"{key}_jsonerr"] = (
            f"Invalid JSON ({exc}). The list above is unchanged and is what the run "
            "will use — fix the text, or edit the list directly.")
        return
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        st.session_state[f"{key}_jsonerr"] = (
            "This must be a JSON list of venues. The list above is unchanged.")
        return
    st.session_state[f"{key}_venues"] = [e for e in (_normalize_venue(i) for i in parsed) if e]
    st.session_state.pop(f"{key}_jsonerr", None)


def _sig_chips(entry, shown=""):
    """The pills saying what an entry will be matched on, strongest first.

    Each pill names **the kind of identifier and its value**, and the run of them
    is introduced by a label.  Both were missing and both were needed: a bare
    ``2771-9987`` is not obviously an ISSN, and a pill reading only ``name`` says
    nothing whatever on its own.  Read with the label in front — *matches on ·
    ISSN 2771-9987 · name* — each pill finishes the sentence it started, which is
    the whole of what these are for: this row is not a description of a journal,
    it is a statement of what the run will look for.

    *shown* is the row's display name.  An entry carrying nothing but an ISSN is
    displayed *as* that ISSN, and repeating the number in the pill beside itself
    read as two identifiers where there is one — so that pill says the word only.
    """
    chips = []
    for issn in entry.get("issns") or []:
        chips.append(
            f"<span class='bl-sig bl-sig-issn' title='The journal&#39;s ISSN. A paper "
            f"is matched when its own ISSN is exactly this — the surest of the three, "
            f"because an ISSN belongs to one journal and nothing else.'>"
            f"{'ISSN' if issn == shown else f'ISSN {html.escape(issn)}'}</span>")
    if entry.get("domain"):
        shared = venue_lookup.site_is_shared(f"https://{entry['domain']}")
        why = ("The venue's website. A paper is matched when its venue sits on this "
               "host or any host under it — and this one looks like a shared publisher "
               "platform, so it covers every journal hosted there, not just this one."
               if shared else
               "The venue's website. A paper is matched when its venue sits on this "
               "host or any host under it.")
        chips.append(f"<span class='bl-sig bl-sig-site{' bl-sig-wide' if shared else ''}' "
                     f"title='{html.escape(why, quote=True)}'>"
                     f"{'⚠ ' if shared else ''}site {html.escape(entry['domain'])}</span>")
    if entry.get("name"):
        chips.append("<span class='bl-sig' title='The venue&#39;s name. A paper is matched "
                     "when this name appears as a whole word inside the paper&#39;s own "
                     "venue name — so a short or common name can also match journals you "
                     "did not mean.'>venue name</span>")
    if not chips:
        return ""
    return "<span class='bl-vl-on'>matches on</span>" + "".join(chips)


def _venue_row_html(entry):
    shown = _venue_name(entry)
    return (f"<div class='bl-vl-row'><span class='bl-vl-name'>"
            f"{html.escape(shown)}</span>{_sig_chips(entry, shown)}</div>")


def _lookup(key, term, source):
    """A venue box's ``search_function`` — the matches for what has been typed.

    Dispatches to whichever directory this list searches, and records what the
    lookup found in ``<key>_last`` so the line under the box can report it on the
    rerun that follows.  An empty box returns nothing, which is what keeps the
    menu shut until something is typed: the component has no results to draw and
    so draws no menu.
    """
    term = (term or "").strip()
    if term != (st.session_state.get(f"{key}_last") or {}).get("term", ""):
        # A different search starts from the first page again; only Show more
        # (which re-runs this with the term unchanged) grows the window.
        st.session_state[f"{key}_limit"] = _LOOKUP_LIMIT
    if not term or not source:
        st.session_state[f"{key}_last"] = _LOOKUP_IDLE
        return []

    limit = st.session_state.get(f"{key}_limit") or _LOOKUP_LIMIT
    if source == "doaj":
        if not doaj.have_local():
            # Nothing to search, and nothing this box can do about it: say where
            # the switch is rather than reporting "no match" for a directory that
            # was never consulted.
            st.session_state[f"{key}_last"] = {
                "term": term, "shown": 0, "total": 0, "pending": False,
                "error": "DOAJ is not on this machine — switch it on in Settings, "
                         "or type the venue and press Add."}
            return []
        index, error = doaj_index()
        venues, total = ((), 0) if error else doaj.search(index, term, limit)
        pending = False
    elif crossref.have_local():
        # A file on this machine. Scanned in ~15ms, so there is nothing to hold
        # the first letters back for — the floor below exists to save a request,
        # and there is no request. Same treatment as DOAJ, which is read this way
        # too. See the Settings page.
        index, error = crossref_index()
        venues, total = ((), 0) if error else crossref.search_local(index, term, limit)
        pending = False
    else:
        result = crossref_search(term, limit)
        error, venues, total = result.error, result.venues, result.total
        # Too short to send is not a failure — it is a question not yet worth a
        # round trip.  Said plainly rather than as "no match".
        pending = len(term) < crossref.MIN_TERM

    if error:
        st.session_state[f"{key}_last"] = {"term": term, "shown": 0, "total": 0,
                                           "error": error, "pending": False}
        return []

    # Labels are made unique before they are offered: a directory can hold two
    # records that reduce to the same line, and the component keys options by
    # label.
    options, seen = [], set()
    for venue in venues:
        label = venue_lookup.option_label(venue)
        while label in seen:
            label += "\u2009"              # thin space; invisible, unique
        seen.add(label)
        options.append((label, venue))
    # The rest of the matches are offered rather than dropped.  A menu showing
    # eight of two hundred, silently, claims to be the answer when it is a
    # sample; this last row says how many are missing and fetches the next page.
    remaining = total - len(options)
    if remaining > 0:
        options.append((f"▾  Show {min(remaining, _LOOKUP_MORE)} more "
                        f"({remaining} further match(es))", _SHOW_MORE))
    st.session_state[f"{key}_last"] = {"term": term, "shown": len(options) - bool(remaining),
                                       "total": total, "error": "", "pending": pending}
    return options


def _reset_box(key):
    """Empty the venue box, the way its own clear-on-submit does.

    The React side is remounted by changing the key it was given, which is why
    the stored key carries a timestamp; writing the search term alone would leave
    the box still showing it.  Guarded, because this reaches into the component's
    own state shape: if a future version changes it, the box simply keeps its
    text instead of the page breaking.
    """
    try:
        st.session_state[f"{key}_box"] = {
            "result": None,
            "search": "",
            "options_js": [],
            "key_react": f"{key}_box_react_{time.time()}",
        }
    except Exception:
        pass


def _remount_box(key):
    """Rebuild the React side while keeping everything Python knows about it.

    Changing ``key_react`` is what remounts the component; unlike
    :func:`_reset_box` this keeps the term and the options, because the one case
    that needs it — turning a page of results — must throw away neither.

    Why a page turn needs it at all: the React side holds the option the user
    chose, and re-sends it on the next render.  ``_refresh_options`` has just
    made that option's *index* mean something else — the row that said "Show 20
    more" is now the ninth of twenty-nine venues — so the re-send resolves to a
    venue nobody picked and adds it.  Measured: turning one page of a "journal
    of" search put *Journal of Fungi* on the list.  Remounting drops the pending
    selection, which is the only way to stop the re-send; ``result`` is cleared
    with it so a later, deliberate pick of the same venue still registers.

    The term survives because it is handed back to the component as
    ``default_searchterm`` on every render — see :func:`_venue_input`.

    **What it costs.**  A remount is the only lever there is: the value a
    component last sent is replayed from Streamlit's widget state on every
    render, and only a new widget id — which is what ``key_react`` is — retires
    it.  A remounted react-select has no focus, so the menu comes back *closed*:
    after Show more the note reads "28 of 4521" and the longer list is one click
    on the box away.  The alternative was to leave the menu open and suppress the
    replay by pretending the venue at that index had already been chosen, which
    would have made that one venue silently unpickable until the user picked
    something else — an extra click is better than a control that ignores you.
    """
    box = st.session_state.get(f"{key}_box")
    if isinstance(box, dict):
        box["result"] = None
        box["key_react"] = f"{key}_box_react_{time.time()}"


def _refresh_options(key, source):
    """Re-run the current search and hand the result straight to the component.

    ``search_function`` fires on a keystroke, and Show more is a *selection* — so
    without this the menu would keep the options it already had until the user
    typed another letter.  The component reads its options from these two
    session-state entries on every render, so rewriting them here is what makes
    the longer list appear.  ``key_react`` is deliberately left alone: it is what
    forces a remount, and remounting would clear the term.
    """
    box = st.session_state.get(f"{key}_box")
    if not isinstance(box, dict):
        return
    options = _lookup(key, box.get("search") or "", source)
    box["options_js"] = [{"label": label, "value": i}
                         for i, (label, _value) in enumerate(options)]
    box["options_py"] = [value for _label, value in options]


def _add_pick(key, chosen, source):
    """A venue box's ``submit_function`` — something in the menu was chosen.

    What arrives is whatever ``search_function`` put in the option's slot, and it
    is accepted on the strength of **what it can do** rather than which class it
    is.  ``isinstance(chosen, venue_lookup.Venue)`` was the obvious test and was
    wrong twice over:

      * It can be False for a value that really is a venue.  ``doaj_index`` is a
        ``cache_resource``, so the journals in it are built once and live for the
        process; reload ``venue_lookup`` — which Streamlit does when a local
        module it imported changes — and the class object is replaced while those
        instances still answer to the previous one.  The whitelist is the list
        exposed to this, because the blacklist's matches come through
        ``cache_data``, which rebuilds its values against whatever class is
        current.
      * When it was False, this function simply **returned**.  No row, no
        message, nothing in the log: "I click a match and nothing appears".

    So the test is what the value can do, and the failure says so out loud.

    **And it reruns before it returns.**  This is a ``submit_function``, which the
    component calls *while the page is being drawn* — the box above it has already
    been rendered by the time this is reached.  So everything written here lands
    in session state one render too late:

      * ``_reset_box`` changes the key that remounts the React side, which is the
        only thing that empties the box.  Without a rerun the box keeps the term,
        and empties later, in the middle of whatever the user typed next — the
        "randomly empties the input", and the remount arriving late is the shift
        up and down that comes with it.
      * The state it leaves behind is inconsistent in a way the *next* click
        trips over: ``_reset_box`` writes no ``options_py``, and the component
        falls back to handing on the raw option index when that key is missing.
        An integer has no ``to_entry``, so the click after an add was dropped —
        "it doesn't add, then next time it does".

    Rerunning here settles all of it at the moment of the click: the component
    remounts empty, and the options it holds and the options recorded for it are
    written in the same pass.  The searchbox does the same thing for its own
    ``clear_on_submit``, from this same place.
    """
    if chosen == _SHOW_MORE:
        st.session_state[f"{key}_limit"] = (
            (st.session_state.get(f"{key}_limit") or _LOOKUP_LIMIT) + _LOOKUP_MORE)
        _refresh_options(key, source)
        _remount_box(key)               # or the stale index re-resolves — see there
        st.rerun()                      # a page turn, not a venue
    entry = None
    if hasattr(chosen, "to_entry"):
        try:
            entry = chosen.to_entry()
        except Exception:
            entry = None
    if entry:
        _add_venue(key, entry)
        _reset_box(key)
        # The box is emptied, so the line under it has to stop reporting the
        # search that filled it — otherwise an empty box sits under "8 of 214".
        st.session_state[f"{key}_last"] = _LOOKUP_IDLE
    else:
        # Nothing usable came back. Whatever the cause, the one thing this must
        # not do is what it used to: swallow the click and leave the list looking
        # as though the user had not chosen anything.
        notify_next_run("That match could not be read. Type the venue and press "
                        "Add instead.", icon="⚠️")
    st.rerun()


def _add_typed_venue(key):
    """Add whatever is in the box, exactly as typed (the Add button).

    Read from the component's own live search term rather than from a committed
    value, because the whole point of this button is the case where nothing was
    committed: the directory offered nothing to choose, so there is only what was
    typed.
    """
    if st_searchbox is None:
        term = st.session_state.get(f"{key}_fallback") or ""
    else:
        term = (st.session_state.get(f"{key}_box") or {}).get("search") or ""
    entry = _entry_from_text(term)
    if entry:
        _add_venue(key, entry)
        st.session_state[f"{key}_last"] = _LOOKUP_IDLE
        if st_searchbox is None:
            st.session_state[f"{key}_fallback"] = ""
        else:
            _reset_box(key)


def _venue_note(key, source):
    """The one line under the box: what the last keystroke's lookup found."""
    if not source:
        return "Added exactly as typed — a name, a website (ieee.org) or an ISSN."
    directory = directory_of(source)
    last = st.session_state.get(f"{key}_last") or _LOOKUP_IDLE
    if last["error"]:
        return last["error"]
    term, shown, total = last["term"], last["shown"], last["total"]
    if not term:
        return f"Start typing — {directory['name']} is searched as you go."
    if last.get("pending"):
        return (f"Keep typing — {directory['name']} is searched from "
                f"{crossref.MIN_TERM} letters, or press Add to use “{term}” as typed.")
    if not shown:
        return f"No {directory['name']} match for “{term}” — press Add to use it as typed."
    counted = f"{shown} of {total}" if total > shown else f"{shown}"
    return (f"{counted} {directory['name']} match(es) — choose one, or press Add to "
            f"use “{term}” as typed.")


def _has_matches(key):
    """Whether the component is currently holding anything to put in a menu.

    Read from the component's own option state, in the same render that styles
    the component, so the two cannot disagree.  Absent on the first render, which
    is exactly the untouched box — and the right answer for it.
    """
    return bool((st.session_state.get(f"{key}_box") or {}).get("options_js"))


def _venue_input(key, source):
    """The one control each list is built with: type a venue, or pick a match.

    A search-as-you-type box rather than a dropdown.  Streamlit's own selectbox
    filters *client-side* over options it was given, so it cannot look anything
    up while a word is being typed.  ``st_searchbox`` calls back into Python on
    each keystroke, so "I" already offers journals, and the menu exists only when
    there is something to show.

    Returns the one-line note that goes under it, which is always present and
    always the same height — it is the only feedback the search gives, and a note
    that appeared only when it had something to say would move the list below it.
    """
    directory = directory_of(source)
    entry_help = ("Read as a name, a website (ieee.org) or an ISSN (1234-5678) — which one "
                  "is decided by what you type, and shown on the row it becomes.")
    # Add belongs to the text, so it appears with it and sits *inside* the box at
    # the right-hand end.  On an empty box there is nothing to add and a button
    # offering to do it is an invitation to press something that cannot work.
    typed = bool((st.session_state.get(f"{key}_box") or {}).get("search")
                 or st.session_state.get(f"{key}_fallback"))
    with st.container(key=f"venuebox-{key}"):
        if st_searchbox is None:
            # The component is missing (an un-updated checkout). The list still
            # works by hand and by JSON; only the lookup is gone.
            st.text_input("Add a venue", key=f"{key}_fallback",
                          label_visibility="collapsed",
                          placeholder="Name, website or ISSN", help=entry_help)
        else:
            st_searchbox(
                (lambda term: _lookup(key, term, source)) if source else (lambda _t: []),
                key=f"{key}_box",
                placeholder="Name, website or ISSN",
                label=None,
                # What the box comes back holding whenever the React side is
                # remounted. It is the term the component itself last recorded,
                # so a remount that is meant to keep the search keeps it
                # (_remount_box, after a page turn) and one that is meant to
                # clear it clears it (_reset_box, after a venue is added, which
                # sets this to ""). Without it every remount emptied the box.
                default_searchterm=(st.session_state.get(f"{key}_box") or {}).get("search") or "",
                # The menu lives *inside* the component's iframe, so the frame
                # has to grow to ~350px to show it — and in normal flow that
                # pushes the list and the JSON fold a third of a screen down
                # every time a letter is typed, and leaves the two columns at
                # different heights.  Positioned absolutely it overlays what is
                # beneath instead, which is what a dropdown should do anyway.
                style_absolute=True,
                # The box is a box, not a menu that happens to accept typing.
                # A chevron promises a list to open, and on an empty box there
                # is none — the same reason "No options" is hidden rather than
                # shown under a control nobody has typed into yet. The menu's
                # own colours come with it, because the stylesheet cannot reach
                # inside the component's iframe (see searchbox_styles).
                style_overrides=searchbox_styles(has_matches=_has_matches(key)),
                # Show more is a selection, and a selection normally ends the
                # search — it would empty the box and close the menu, throwing
                # away the term whose next page was just asked for.  Both are
                # turned off and the clearing is done in the handler instead,
                # which knows the difference between turning a page and choosing
                # a venue.
                clear_on_submit=False,
                edit_after_submit="current",
                debounce=directory["debounce"] if directory else 150,
                submit_function=lambda chosen: _add_pick(key, chosen, source),
                help=(entry_help + f" Typing searches {directory['name']} as you go, "
                      + ("from a copy held on this machine — no internet needed; "
                         if directory["local"] else "over the internet; ")
                      + "choosing a match adds that venue with its official title and "
                      f"ISSN. Being listed in {directory['name']} is not part of any "
                      "verdict — it only helps name a venue." if directory else entry_help))
        if typed:
            st.button("Add", key=f"{key}_handadd",
                      on_click=_add_typed_venue, args=(key,),
                      help="Add exactly what is in the box, whether or not the "
                           "directory knows it.")
    return _venue_note(key, source)


def _venue_list_body(key, empty):
    """The rows themselves, inside the fixed-height box that never resizes."""
    venues = st.session_state.get(f"{key}_venues") or []
    if not venues:
        st.markdown(f"<div class='bl-vl-empty'>{empty}</div>", unsafe_allow_html=True)
        return
    for i, entry in enumerate(venues[:_MAX_SHOWN_VENUES]):
        row = st.columns([8.4, 1.6], vertical_alignment="center")
        row[0].markdown(_venue_row_html(entry), unsafe_allow_html=True)
        row[1].button("✕", key=f"{key}_rm_{i}", width="stretch",
                      on_click=_remove_venue, args=(key, i),
                      help=f"Remove {_venue_name(entry)}")
    if len(venues) > _MAX_SHOWN_VENUES:
        st.caption(f"…and {len(venues) - _MAX_SHOWN_VENUES} more not listed here. "
                   f"All {len(venues)} are in force; the full list is under "
                   "**Edit as JSON**.")


def venue_list_editor(label: str, key: str, hint: str, empty: str,
                      *, source: str = ""):
    """One venue list, edited as a list. Returns the entries, or None if empty.

    The return value is the same JSON-shaped list the old text box parsed to, so
    everything downstream — the run summary, the temp file, the CLI flag — is
    unchanged.

    Both lists render the **same skeleton in the same order** — heading, one
    combobox with its Add button, a one-line note, list, JSON fold — and every
    band in it has a height that does not depend on its contents.  That is what
    makes the two columns line up and stay lined up: the whitelist and the
    blacklist put their controls on the same rows because they are literally the
    same rows.  The only difference between them is what the combobox offers,
    which costs no height at all.

    The controls come first and the list follows, because the list is the only
    part that grows: entries appear at the top of a box whose position is fixed,
    rather than pushing the controls that created them down the page.

    *empty* is what the list says when it holds nothing, and the two lists must
    say different things: an empty whitelist means every paper is checked, while
    an empty blacklist means Beall's List is used as it stands.  One sentence
    covering both would have to be vague about the only fact worth stating.
    """
    st.session_state.setdefault(f"{key}_venues", [])
    st.session_state.setdefault(key, "")
    venues = st.session_state[f"{key}_venues"]

    # Heading and hint as one block, so a two-line hint and a three-line one
    # occupy the same band and the add boxes beneath them share a row.
    st.markdown(f"<div class='bl-vl-head'><div class='bl-vl-label'>{label}</div>"
                f"<div class='bl-vl-hint'>{hint}</div></div>", unsafe_allow_html=True)

    note = _venue_input(key, source)
    # Always rendered, always one line: this is the search's only feedback, and a
    # note that appeared only when it had something to say would take the list
    # below it up and down the page as the query changed.
    st.markdown(f"<div class='bl-vl-note'>{html.escape(note)}</div>",
                unsafe_allow_html=True)

    # A fixed height with its own scrollbar: the one part of this column whose
    # contents grow, given a size that does not.  Streamlit's `height=` is used
    # rather than a stylesheet rule so the scroll behaviour is the container's
    # own and survives a change to its internals.
    with st.container(height=styles.VENUE_BOX_HEIGHT, border=True, key=f"venuelist-{key}"):
        _venue_list_body(key, empty)

    with st.expander("{ } Edit as JSON", expanded=False):
        st.text_area(
            "Venue list as JSON", key=key, height=150, label_visibility="collapsed",
            on_change=_venue_json_edited, args=(key,),
            placeholder='[{"name": "IEEE", "domain": "ieee.org"}, "Nature"]',
            help="The same list, in the format the check reads. Edits here update the list "
                 "above when the box loses focus (or on Ctrl+Enter).")
        error = st.session_state.get(f"{key}_jsonerr")
        if error:
            st.error(error)
        st.caption('A JSON list of `{"name", "domain", "issns"}` objects, or bare strings '
                   'like `"Nature"` to match by name alone. Paste one from a colleague or a '
                   'previous run; it appears as rows above.')
        # Always rendered, disabled when there is nothing to clear: a button that
        # comes and goes with the list would move the fold's own contents.
        st.button("Clear the list", key=f"{key}_clear", on_click=_clear_venues,
                  args=(key,), disabled=not venues,
                  help="Remove every entry from this list.")

    return list(venues) or None


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
# styles.PAGE_CSS).  Widths are `fr` units, so they behave like the column ratios they
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
        # Counted from the list itself rather than from the JSON box's text, so
        # the pill says how many venues are in force instead of only that the box
        # is not empty — and cannot disagree with the rows drawn below it.
        for name, key in (("whitelist", "wl"), ("blacklist", "bl")):
            n = len(st.session_state.get(f"{key}_venues") or [])
            if n:
                bits.append(f"{name} ({n})")
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
    st.markdown(styles.PAGE_CSS, unsafe_allow_html=True)
    # The one pair of colours the stylesheet cannot work out for itself.
    st.markdown(styles.field_placeholder_css(), unsafe_allow_html=True)
    # Anything the previous render queued on its way out — see notify_next_run.
    flush_notices()

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
    st.markdown(styles.ROW_TINT_CSS.format(key=_row_key(checker.id)), unsafe_allow_html=True)

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
    llm_ok, llm_msg = None, ""          # also read by Step 4's summary, below
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

    # Step 3 — options.  Nothing here has to be touched, so the pill states what
    # is in force rather than whether anything was.
    #
    # The hint used to read "all optional", which was true and unhelpful in the
    # same breath.  Every other step's hint says what the step is *about*; this
    # one described its obligation level instead, and did it in a word that reads
    # as "ignorable" — which one of these settings, the one that decides what the
    # run spends money on, is not.  It now says what the others say: these have
    # values already, and you change the ones this run needs.
    #
    # Nothing in this step is folded away any more.  Every control here changes
    # what the run does — Crossref moves two of the example corpus's eight
    # records between verdicts on its own — and a step whose header claims to
    # summarise the settings in force cannot do that while some of them are
    # behind a click.
    step_header(3, "Options", "already set — change what this run needs",
                _options_state(checker), learn="venue lists")

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
                       "To have it offered in the list from now on, add it to `.env` and "
                       "restart the app — `.env` is read once, at startup.")

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
    # anyway, holding a note that said so, which made the section a control that
    # promised two things and then apologised for having neither.  Step 4's
    # summary already reports the same fact as a property of the run rather than
    # as a dead end, which is where it belongs.
    # In the open, not behind a fold.  They were folded away for being rare, but
    # rare is not the same as advanced, and a fold answers the wrong question: it
    # hides *what the run is doing* behind a click, so the only way to confirm no
    # venue list is in force is to open a panel and read two empty boxes.  Shown,
    # they answer that by being visibly empty.
    #
    # Side by side rather than stacked, which is what the fold was really buying:
    # two 90px boxes in a column pushed the Run button off the screen, and these
    # two are a pair — one narrows the run, the other widens it — so a row reads
    # them as the opposites they are.
    wl = bl = None
    if checker.kind != "csv":
        st.markdown("**🎯 Venue lists — narrow the run, or add venues of your own**")
        st.caption("Both are empty on most runs. Leave them so unless this run needs a "
                   "different scope from Beall's List itself.")
        wl_col, bl_col = st.columns(2)
        # Named by what they do, with the familiar term kept as a secondary
        # identifier.  "Whitelist" alone is ambiguous here: it reads just as
        # easily as "venues I consider safe", which is the opposite of what
        # it does.
        with wl_col:
            wl = venue_list_editor(
                "Only check these venues &nbsp;·&nbsp; <b>whitelist</b>", "wl",
                "Restricts the run. A paper from any venue not listed here is reported as "
                "<code>out_of_scope</code> — <b>not examined</b>, which is not the same as "
                "cleared. Leave empty to check every paper.",
                "Empty — every paper is checked.",
                # DOAJ indexes journals, not predatory publishers, so it can help
                # build this list and has no business building the other one:
                # appearing in DOAJ is evidence a journal was vetted, which is
                # the opposite of a reason to treat it as predatory.
                source="doaj")
        with bl_col:
            bl = venue_list_editor(
                "Also treat these venues as predatory &nbsp;·&nbsp; <b>blacklist</b>", "bl",
                "Extends the list. These venues count as predatory for this run in "
                "addition to everything on Beall's List.",
                "Empty — Beall's List is used as it stands.",
                # Crossref, not DOAJ: a venue worth blacklisting is one no
                # directory of vetted journals will have, and the DOI registry
                # screens nobody. See _DIRECTORIES.
                source="crossref")
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
        #
        # The cell reports readiness as well as identity, which is what the
        # sidebar's Environment panel used to be for.  This is the better place
        # for it: the summary exists to be read immediately before pressing Run,
        # and "which LLM" and "will it answer" are one question at that moment.
        # Step 1's line still says it first, when the check is chosen; this says
        # it last, next to the button.
        if not model:
            llm_cell = (f"{provider} / no model chosen", "bl-sum-todo",
                        "Name a model in Step 3. Without one the run falls back to "
                        "whatever .env configures, which may not be a model this "
                        "provider serves.")
        elif llm_ok:
            llm_cell = (f"{provider} / {model}", "", "")
        else:
            # Unverified (ok is None) reads the same as unconfigured here: either
            # way the run is about to start on an answer nobody has.
            llm_cell = (f"⚠️ {provider} / {model}", "bl-sum-todo",
                        llm_msg or "The LLM configuration could not be verified.")
        summary.insert(1, ("LLM", *llm_cell))

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
    st.markdown(styles.TUTORIAL_CSS, unsafe_allow_html=True)
    with st.container(key="tutorial"):
        _render_markdown_file(_TUTORIAL_MD, skip_app_only=True,
                              static_url="/app/static/",
                              before_sections=_contents_html())


def _bytes_human(n):
    """A size a person reads, from a number of bytes."""
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{max(n // 1000, 1)} kB"


def _age_human(days):
    """How long ago, in the largest unit that still says something useful."""
    if days < 1 / 24:
        return "just now"
    if days < 1:
        return f"{int(days * 24)} hour(s) ago"
    return f"{int(days)} day(s) ago"


def _para(*paragraphs):
    """Join paragraphs for a tooltip, which renders Markdown and wants blank lines."""
    return "\n\n".join(paragraphs)


def _directory_row(module, *, title, key, offered, coverage, off_state, help_text):
    """One directory, as a switch and one line of fact.

    A switch rather than a Download button and its retinue, because there are only
    two states worth having and the switch *is* both of them: on means the list is
    held here and searched here; off means it is not.  There is no separate Remove
    and no separate Refresh — off removes it, and a copy older than a fortnight is
    replaced on its own.

    Everything that used to be said in paragraphs is on the ⓘ beside the switch.
    A settings page is read when something needs deciding, and the decision is the
    switch; the reasoning should be there for whoever wants it and out of the way
    of whoever does not.
    """
    state = module.status()
    held = state["present"]
    want = st.toggle(f"**{title}** — searched by the {offered}", value=held,
                     key=f"hold_{key}", help=help_text)

    if want and not held:
        _fetch_directory(module, title)         # reruns when it lands
    elif held and not want:
        module.delete()
        clear_directory_caches()
        notify_next_run(f"{title} removed from this machine.", icon="🗑️")
        st.rerun()

    if held:
        count = (f"{state['journals']:,} journals" if state["journals"] >= 0
                 else "unreadable — switch it off and on again")
        line = (f"{count} &nbsp;·&nbsp; {_bytes_human(state['bytes'])} stored "
                f"&nbsp;&nbsp;—&nbsp;&nbsp; {coverage}")
        tone = "bl-dir-present"
    else:
        line, tone = off_state, "bl-dir-absent"
    st.markdown(f"<div class='bl-dir-state {tone}'>{line}</div>", unsafe_allow_html=True)


def _fetch_directory(module, title):
    """Download one directory's list, with a bar, then forget the old index."""
    bar = st.progress(0.0, text=f"Fetching {title}…")
    try:
        def on_progress(fraction):
            # A server that sends no length gives None; the bar then only says
            # "running" rather than lying about how far along it is.
            bar.progress(fraction if fraction is not None else 0.5,
                         text=f"Fetching {title}…"
                              + (f" {int(fraction * 100)}%" if fraction else ""))

        journals, error = module.load_journals(refresh=True, progress=on_progress)
        bar.progress(1.0, text=f"Reducing {len(journals):,} journals…")
    except Exception as exc:                      # a download must not break the page
        journals, error = [], f"{type(exc).__name__}: {exc}"
    finally:
        bar.empty()
    clear_directory_caches()
    if error:
        st.error(error)
    else:
        notify_next_run(f"{title}: {len(journals):,} journals held here.", icon="✅")
        st.rerun()


def page_settings():
    """Two switches: whether each venue directory is held on this machine.

    The page exists because the default has to stay cheap.  Nothing here is
    downloaded until it is switched on, which is the whole point — a first run of
    this app should cost nothing it was not asked for.
    """
    st.markdown(styles.PAGE_CSS, unsafe_allow_html=True)
    st.markdown(styles.field_placeholder_css(), unsafe_allow_html=True)
    flush_notices()

    st.title("⚙️ Settings")
    st.caption("Hold a venue directory on this machine and Step 3 searches it here — "
               "instantly, with no internet. Nothing is downloaded until you switch it "
               "on. Neither directory takes any part in any verdict.")

    _directory_row(
        doaj, title="DOAJ", key="doaj", offered="**whitelist**",
        coverage="every one has a name, an ISSN, a publisher and a website",
        off_state="Not on this machine — the whitelist cannot search until you switch this on",
        help_text=_para(
            "The Directory of Open Access Journals: about 23,000 journals that passed "
            "its editorial screening, each with its name, ISSN, publisher and website. "
            "That is everything a whitelist entry can match on.",

            "Switching this on downloads about 25 MB once and keeps about 4 MB of it — "
            "the rest is fields a venue list has no use for. A few seconds.",

            "There is no online alternative for this box: DOAJ's search endpoint "
            "answers a request per keystroke with a 502, so the whitelist can only "
            "search a local copy. With this off, type the venue and press Add.",

            "Being listed in DOAJ is not part of any verdict — it only helps name a "
            "venue."))

    _directory_row(
        crossref, title="Crossref", key="crossref", offered="**blacklist**",
        coverage="every one has a name, 82% an ISSN, almost all a publisher — no websites",
        off_state="Searching Crossref online — about half a second a search, "
                  "needs a connection, starts from two letters",
        help_text=_para(
            "The DOI registry: about 167,000 journals, screened by nobody, which is "
            "what a blacklist needs. Every one has a name and almost all a publisher; "
            "82% carry an ISSN. Crossref records no websites at all, so an entry added "
            "from here matches on name and ISSN, never on a domain.",

            "Switching this on downloads about 51 MB once and keeps about 18 MB. "
            "Twenty seconds or so.",

            "With it off the blacklist still works — it asks Crossref's live API "
            "instead, which needs a connection and takes about half a second a search. "
            "With it on, searches are local and instant, and start from one letter.",

            "Being registered with Crossref is not part of any verdict — it only helps "
            "name a venue."))

    st.caption(f"Stored in `{venue_lookup.CACHE_DIR}` · ignored by git · safe to delete "
               "by hand · replaced automatically once older than a fortnight.")


# ── Navigation ──────────────────────────────────────────────────────────────
# Three pages in the sidebar: the flow, the instructions for it, and the one
# thing there is to configure.
nav = st.navigation([
    # The default page is served from "/", so it is given no url_path of its own.
    st.Page(page_check, title="Run a check", icon="▶️", default=True),
    st.Page(page_tutorial, title="Tutorial", icon="📘", url_path="tutorial"),
    st.Page(page_settings, title="Settings", icon="⚙️", url_path="settings"),
])
sidebar()          # below the page links, on every page
nav.run()
