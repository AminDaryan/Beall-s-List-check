"""Streamlit UI for the predatory-venue check.

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

import hashlib
import html
import json
import os
import re
from typing import NamedTuple

import streamlit as st
from dotenv import load_dotenv

import sys
from pathlib import Path

# Flat imports across ui/'s feature folders — `import styles`, not
# `from ui.styles import styles`.  ui/ is not a package (Streamlit runs app.py as
# a script), so every folder is put on the path first.  Repeated per module
# rather than shared, so each still imports when it is the thing being run: from
# a shell, or by anything that never touched app.py.  Same shim the check
# engine's own foldered modules use — see src/bealls_list_check/*/.
_UI = Path(__file__).resolve().parent
for _p in [_UI, *(d for d in _UI.iterdir() if d.is_dir() and any(d.glob("*.py")))]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import components
import notices
import results
import runners
import reference_lists
import settings_page
import styles
import tutorial_page
import venue_lists

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

st.set_page_config(page_title="Predatory venue check", page_icon="🕵️", layout="wide")
# Best-effort: show Streamlit toasts in the top-right (falls back to default).
st.markdown("<style>div[data-testid='stToast']{position:fixed;top:3.5rem;right:1rem;}</style>",
            unsafe_allow_html=True)


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


# The provider `.env` names. Step 3 starts from it and can be pointed elsewhere
# for a single run, so it is a default rather than the answer.
ENV_PROVIDER = os.getenv("LLM_PROVIDER", "azure").lower()

# A model this machine's .env has not been told about cannot be offered in a
# list, but that is no reason to refuse to run it.  There used to be an "Other —
# type a name…" entry that revealed a second control below the first, which meant
# every provider with no configured names showed a dropdown of one useless option
# and a text box under it: two controls, one question.  The picker now takes
# typed names directly (`accept_new_options`), so there is one control whether
# the name is chosen or typed.


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

    One value either way: the picker accepts a typed name as an option, so a name
    that is not in ``.env`` arrives here the same way a configured one does.

    Empty when nothing is chosen, which is a state the flow reports rather than
    hides: the run is started with whatever this returns, and with nothing it
    falls back to what the environment already names.
    """
    return (st.session_state.get("model") or "").strip()


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
    st.sidebar.markdown("### Predatory venue check")
    st.sidebar.caption(
        "Is a paper's **journal or publisher** on a published list of *potentially* "
        "predatory venues? A match means the **venue appears on a list you chose** — "
        "not that the paper is bad.")
    st.sidebar.caption("📘 New here? Open the **Tutorial** page above.")


@st.cache_data(show_spinner=False)
def _example_corpus_text():
    """The bundled example corpus, or "" when it cannot be read."""
    try:
        return _EXAMPLE_CORPUS.read_text(encoding="utf-8")
    except OSError:
        return ""


STEPS = [("Choose a check", "choose-a-check"), ("Provide the papers", "provide-the-papers"),
         ("Options", "options"), ("Run", "run")]


def step_rail(own_papers, has_result):
    """The four steps as a rail across the top of the page, marking where you are.

    The flow is one long page, so "which screen am I on" has no answer — but
    "how far along am I, and what is still outstanding" does, and that is the
    question the rail answers. A step is *done* when it holds a usable answer,
    *now* when it is the first one that does not, and *ahead* after that.

    *own_papers* is the reader's own corpus, NOT the example this page seeds
    itself with. The rail first read "are there papers", which is true the
    instant the page opens — so a reader who had done nothing was shown three
    ticks and told the only thing left was to press Run. The example is there to
    make the app runnable on arrival, not to stand in for their data, and
    _is_example_corpus exists because Step 2's banner and Step 4's summary had
    already had to make the same distinction.

    Every number is the link to its own step, the same anchor its header badge
    carries, so the rail doubles as the way back to any of them.

    Rendered into a slot reserved before the steps and filled after they have
    run: what to mark depends on what Step 2 loaded and whether Step 4 has a
    result, and Streamlit paints in script order.
    """
    done = [True, bool(own_papers), bool(own_papers), bool(has_result)]
    now = next((i for i, ok in enumerate(done) if not ok), None)
    out = []
    for i, (title, anchor) in enumerate(STEPS):
        state = "done" if done[i] else ("now" if i == now else "ahead")
        if i:
            out.append("<span class='bl-rail-link'></span>")
        out.append(
            f"<a class='bl-rail-step is-{state}' href='#{anchor}'>"
            f"<span class='bl-rail-n'>{'✓' if done[i] else i + 1}</span>"
            f"<span class='bl-rail-t'>{title}</span></a>")
    return f"<nav class='bl-rail' aria-label='Progress through the four steps'>{''.join(out)}</nav>"


def _seed_example_corpus():
    """Open the page with the example already in the paste box. Once per session.

    Almost everyone who opens this app opens it once: it is a tool a colleague is
    pointed at, used for one corpus, and closed.  For that reader an empty page
    is a page that cannot be tried — the only way to find out whether any of it
    works was to supply real data first, which is the wrong order for the one
    question a new user actually has, which is *does this thing work and what
    does it give me*.

    So the app arrives in a state that runs.  The example fills the paste box,
    where it doubles as a worked example of the format the box wants, and where
    replacing it is what anyone would do anyway: select all, paste your own.

    Once, and only on the first render of a session.  Seeding on every rerun
    would make the box impossible to empty — clear it, and the next rerun would
    put it back — so the flag is set before the read, and is not conditional on
    the read succeeding: a missing example file leaves an empty page, not a
    retry on every interaction.

    Assigning to a widget's key before the widget is created is how Streamlit
    takes an initial value, so this has to run before Step 2 renders.
    """
    if st.session_state.get("seeded_example"):
        return
    st.session_state["seeded_example"] = True
    text = _example_corpus_text()
    if not text:
        return
    st.session_state["paste"] = text
    st.session_state["mode"] = "Paste JSON"


def _is_example_corpus():
    """Whether what is loaded is the bundled example rather than the user's data.

    Read by Step 2's banner and Step 4's summary, both of which have to say so:
    a page that arrives holding papers is a page that can be run without anyone
    having supplied anything, and "8 paper(s)" in green is exactly what that
    would look like if it were the user's own.
    """
    return bool(st.session_state.get("loaded_example"))


# ── The corpus, read once per file rather than once per rerun ────────────────
# Streamlit reruns the whole script on every interaction, and Step 2 used to
# re-read and re-parse whatever was in it each time.  On a real corpus that is
# not a rounding error.  Measured on a 60 MB, 18,482-paper export, one keystroke
# in Step 3's venue box cost 0.9s to decode and parse, 7.5s to validate and 3.1s
# to build the quality workbook — 11.5s of script run, against 0.25s with
# nothing loaded.  For all of it Streamlit holds every element it has not
# redrawn yet at its stale opacity (0.33, after a 0.5s delay), which is why
# everything below Step 2 went dim and stayed dim for as long as anyone typed.
#
# So the corpus is now identified rather than re-derived: *token* names which
# corpus this is — the uploader's own file id, or a digest of the pasted text —
# and the three expensive steps are keyed on it.  A rerun that changes neither
# does no work at all.
#
# ``cache_resource`` and not ``cache_data``, for the reason ``doaj_index`` gives:
# cache_data serialises its value and hands back a copy on every access, and a
# parsed copy of that corpus is 117 MB.  One shared list is safe here because
# nothing downstream mutates the records — a run writes them out with
# ``json.dumps`` and reads them nowhere else.  ``max_entries=1`` keeps the corpus
# on screen and lets the one before it go.
@st.cache_resource(show_spinner=False, max_entries=1)
def _parse_corpus(_source, token):
    """The papers *token* names, parsed once.

    *_source* is the uploaded file or the pasted text; it is underscored so that
    Streamlit leaves it out of the cache key — hashing 60 MB on every rerun is
    the cost this exists to avoid, and *token* already answers "which corpus".
    """
    raw = _source.getvalue().decode("utf-8") if hasattr(_source, "getvalue") else _source
    return runners.normalize_upload(json.loads(raw))


def get_papers():
    """Render the input widgets and return ``(papers, token)``.

    *papers* is the parsed list, or None when there is nothing loaded; *token*
    names the corpus for the caches keyed on it (see :func:`_parse_corpus`) and
    is empty when *papers* is None.

    The widget keys are deliberately independent of the selected check, so
    switching between the three rows in Step 1 keeps the loaded papers.
    """
    # Unlabelled: "Upload a file" and "Paste JSON" beside a box that takes papers
    # are not a question anyone needs asked.
    #
    # There was a third thing on this row, a "Load the example corpus" button.
    # The example is in the box when the page opens now, so the button offered to
    # do something already done — and a control whose only job is to reach the
    # state you are already in is worse than no control.
    mode = st.radio("How papers are supplied", ["Upload a file", "Paste JSON"],
                    horizontal=True, key="mode", label_visibility="collapsed")
    source, token, pasted = None, "", ""
    if mode == "Upload a file":
        up = st.file_uploader("Papers file (.json)", type=["json"], key="file",
                              label_visibility="collapsed")
        if up is not None and up.size:
            # Streamlit's own id for the uploaded file, which changes when a
            # different file is chosen and not otherwise — so the parse below
            # happens once per file.  The bytes are deliberately not read here:
            # reading them is what the cache is there to skip.
            source, token = up, f"upload:{up.file_id}"
    else:
        pasted = st.text_area("Paste JSON here", height=180, key="paste",
                              placeholder=_S2_EXAMPLE, label_visibility="collapsed")
        if pasted and pasted.strip():
            # No file id to lean on, so the text names itself.  A digest costs a
            # pass over the string; parsing it costs a pass plus an object per
            # field, which is the part worth not repeating.
            source = pasted
            token = "paste:" + hashlib.sha1(pasted.encode("utf-8")).hexdigest()
    # Recorded rather than re-derived: two places downstream have to say whose
    # papers these are, and comparing the text is this function's business, not
    # theirs.  An edit of a single character makes it the user's own.
    st.session_state["loaded_example"] = bool(
        token and mode == "Paste JSON" and pasted == _example_corpus_text())

    # What the box will accept, under the box that has to accept it.  It was
    # above, where it was read before there was anything to apply it to and had
    # to be scrolled back to afterwards; a format is checked against a file at
    # the moment the file is chosen, which is here.
    st.caption("Input format: Semantic Scholar **paper records** — JSON objects carrying a "
               "`publicationVenue`, not links or titles. A **list** of them, or a "
               "**single** one.")
    with st.expander("See expected format"):
        st.code(_S2_EXAMPLE, language="json")

    if not token:
        return None, ""
    # Both failures are reported here rather than inside the cached function: a
    # raise is not cached, so a file that cannot be read is re-read on every
    # rerun — which is right, since the next rerun may be the one that fixed it.
    try:
        return _parse_corpus(source, token), token
    except json.JSONDecodeError as e:
        st.error(f"Invalid JSON: {e}")
        return None, ""
    except ValueError as e:
        st.error(str(e))
        return None, ""


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


def _heading_id(title):
    """The id Streamlit gives a heading it renders, worked out the same way.

    Lower-cased, punctuation dropped, runs of whitespace hyphenated — so
    "Choose a check" is "choose-a-check". Derived rather than hard-coded so a
    renamed step keeps a working link, and checked against the rendered ids in
    the UI test.
    """
    slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
    return re.sub(r"[\s_]+", "-", slug)


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
    href = tutorial_page.tutorial_href(learn) if learn else ""
    info = (f"<a class='bl-info' href='{href}' target='bl-tutorial' "
            f"title='Read about this step in the Tutorial'>&#9432;</a>") if href else ""
    # The title is a real <h2>, not a styled span.  The page reads as four steps
    # and looked like it, but to anything that navigates by structure — a screen
    # reader's heading list, a browser's outline, "next heading" — it was one <h1>
    # and 380 words of undifferentiated text.  The number stays out of the heading
    # so the heading's text is the step's name; the pill and the hint stay out for
    # the same reason, and because they change while the name does not.
    # The number is the link to this step. Streamlit gives every heading it
    # renders an id and a chain-link icon beside the text; the icon is hidden
    # (see PAGE_CSS) and its job moved onto the number, which is already the
    # thing a reader points at when naming a step. Same destination, one less
    # mark in a header that carries three.
    anchor = _heading_id(title)
    number = (f"<a class='bl-step-n' href='#{anchor}' "
              f"title='Link to this step'>{num}</a>")
    st_.markdown(
        f"<div class='bl-step'>{number}"
        f"<h2 class='bl-step-t'>{title}</h2>{info}"
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
    chosen = [p for p in reference_lists.ids()
              if st.session_state.get(f"list_{p}", p in reference_lists.default_ids())]
    # Named, not counted: which catalogues a run screened against is the thing a
    # reader has to be able to state, and there are few enough to name.
    bits.append(", ".join(reference_lists.label(p) for p in chosen) if chosen
                else "⚠️ no list selected")
    bits.append("Crossref on" if st.session_state.get("cr", True) else "Crossref off")
    # All three checks take both lists now, so the pill reports them for all
    # three.  Batch CSV was the exception only because its script had never been
    # given the flags.
    if True:
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
    _ok, _msg, models = llm_status(selected_provider())
    st.session_state["model"] = models[0] if models else None


def _pick_checker(checker_id: str):
    if st.session_state.get("checker_id") == checker_id:
        return                        # re-click on the selected row: nothing to do
    st.session_state["checker_id"] = checker_id
    # The add-on CSV belongs to a particular result; a new selection invalidates it.
    st.session_state.pop("csv_addon", None)


# ── Pages ────────────────────────────────────────────────────────────────
class _Llm(NamedTuple):
    """What Step 1 worked out about the language model, for Step 4 to restate.

    A named tuple rather than three loose values, for the reason ``_LOOKUP_IDLE``
    is a mapping: these travel together between two steps that are hundreds of
    lines apart, and a positional tuple that grew a field would break the reader
    silently.
    """

    ok: object          # True, False, or None when there is nothing to verify
    message: str
    models: list


class _Options(NamedTuple):
    """Everything Step 3 decides, as Step 4 needs it."""

    crossref: bool
    lists: list
    whitelist: object
    blacklist: object
    aux_lists: dict
    extra_args: object
    provider: str
    model: str


def _step_choose_check(checker):
    """Step 1 — choose a check, and report whether its LLM is usable.

    Returns the :class:`_Llm` that Step 4's summary restates. That it is returned
    rather than left in a local is what the old comment beside it already said:
    "also read by Step 4's summary, below".
    """
    # Step 1 — choose a check.  A comparison table rather than a dropdown: the
    # three are meant to be weighed against each other, and a dropdown shows only
    # the option already chosen.
    step_header(1, "Choose a check", "what to run over your papers",
                f"{checker.icon} {checker.title}", "ok", learn="First run")
    # The second sentence used to read "Hover a Cost, Records or Returns pill for
    # the detail", which is an instruction about the page rather than anything
    # about the checks, and it names the pills by their column headings — which
    # are on screen a line below it, so it points at what it is standing next to.
    # The pills carry hover text; that is a property of the table, not news.
    st.caption("All three run the same deterministic engine. They differ in what they add "
               "on top of it, what they cost, and what you get back.")

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
                # All three priced columns sit in the right half of the table, so
                # every one of their panels opens leftwards. Rightwards, a 430px
                # explanation runs past the edge — which used to give the page a
                # horizontal scrollbar and, once that was clipped, cut the text off.
                results._chip(c.cost_label, "warn" if c.needs_llm else "ok",
                              title=c.cost_note, align="end"),
                results._chip(c.records_label, "mute", title=c.records_note,
                              align="end"),
                results._chip(c.output_label, "mute", title=c.output_note,
                              align="end"),
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
    return _Llm(llm_ok, llm_msg, llm_models)


def _step_provide_papers():
    """Step 2 — the papers themselves.

    Returns ``(records, token, quality_slot)``: the papers or None, the name the
    caches key on (see :func:`_parse_corpus`), and an empty container reserved
    where the data-quality panel goes — filled by the caller, after Steps 3 and
    4, for the reason given there.
    """
    # Step 2 — provide the papers.  The header's container is reserved first and
    # written afterwards: how many papers there are is only known once the widgets
    # below it have run, and the pill would otherwise report the previous rerun.
    head2 = st.container(key="blstep2")
    items, token = get_papers()
    step_header(2, "Provide the papers", "Semantic Scholar records, as JSON",
                f"✅ {len(items)} paper(s)" if items else "waiting for papers",
                "ok" if items else "wait", into=head2, learn="your own papers")
    quality_slot = None
    if items is not None:
        # Whose papers these are, said where the count is said.  The page opens
        # holding the example, so "✅ 8 paper(s)" in green — the same words the
        # user's own corpus gets — would be the app quietly presenting data
        # nobody supplied as data somebody did.  Blue, and it names the file.
        if _is_example_corpus():
            st.info(f"📦 The bundled **example corpus** is loaded — {len(items)} papers from "
                    f"`examples/{_EXAMPLE_CORPUS.name}`, so this page arrives ready to run. "
                    "Run it to see what comes back, then replace it above with your own "
                    "papers. Its expected result is tabulated in `examples/README.md`.")
        else:
            st.success(f"✅ Loaded {len(items)} paper(s) — ready to run.")
        # Reserved here, written last: the panel is the slowest thing on the page
        # and everything below it was waiting for it.  See page_check.
        quality_slot = st.container()
    return items, token, quality_slot


def _options_section(icon, title, tag=""):
    """One bordered block of Step 3, opened as a context manager.

    Step 3 holds two or three unrelated decisions — which LLM answers, whether
    Crossref is consulted, which venues are in scope — and used to run them
    together down one column, separated by nothing but a bold line and a caption.
    Read quickly, that is one long form; the only way to tell where the LLM
    stopped and Crossref started was to read the labels.

    Each is now its own bordered block, so the step is read as the two or three
    questions it is.  A heading here is a *label on a block*, not a description
    of it: two words at most, plus *tag* — one word on whether the block has to
    be touched at all.  It said more once ("empty on most runs — one narrows the
    run, the other widens it"), which is a sentence worth reading once and then
    in the way every time after, and it is one the controls below say anyway.
    """
    box = st.container(border=True)
    tag = f"<span class='bl-opt-s'>{tag}</span>" if tag else ""
    # <h3>, under Step 3's <h2>: these are subsections of one step, and saying so
    # is what makes a heading list a table of contents rather than a flat list.
    box.markdown(f"<div class='bl-opt-h'><span class='bl-opt-i'>{icon}</span>"
                 f"<h3 class='bl-opt-t'>{title}</h3>{tag}</div>",
                 unsafe_allow_html=True)
    return box


def _venue_list_section():
    """Step 3's two venue lists, side by side. Returns ``(whitelist, blacklist)``.

    Not shown at all for Batch CSV, which takes neither — batch_check.py has no
    --whitelist or --blacklist to give them to.  It used to be shown anyway,
    holding a note that said so, which made the section a control that promised
    two things and then apologised for having neither.  Step 4's summary already
    reports that as a property of the run rather than as a dead end.

    In the open, not behind a fold.  They were folded away for being rare, but
    rare is not the same as advanced, and a fold answers the wrong question: it
    hides *what the run is doing* behind a click, so the only way to confirm no
    venue list is in force is to open a panel and read two empty boxes.  Shown,
    they answer that by being visibly empty.

    Side by side rather than stacked, which is what the fold was really buying:
    two boxes in a column pushed the Run button off the screen, and these two are
    a pair — one narrows the run, the other widens it — so a row reads them as
    the opposites they are.
    """
    with _options_section("🎯", "Venue lists"):
        wl_col, bl_col = st.columns(2)
        # Named by the familiar term, with everything else on the ? beside it.
        # The heading carried the explanation itself once — first as a longhand
        # title beside the word, then as a paragraph under it — and either way
        # the two widest lines in this step were spent saying, on every look,
        # what is read once and then known.  It is not dropped: "whitelist"
        # alone is ambiguous here ("venues I consider safe" is the opposite of
        # what it does), so the answer stays one hover away.
        with wl_col:
            wl = venue_lists.venue_list_editor(
                "Whitelist", "wl",
                "Only check these venues. Only papers from these venues are checked, and "
                "only they appear in the report — the rest are left out of it entirely, "
                "not listed as skipped. Leave empty to check every paper.",
                "Empty — every paper is checked.",
                # DOAJ indexes journals, not predatory publishers, so it can help
                # build this list and has no business building the other one:
                # appearing in DOAJ is evidence a journal was vetted, which is
                # the opposite of a reason to treat it as predatory.
                source="doaj")
        with bl_col:
            bl = venue_lists.venue_list_editor(
                "Blacklist", "bl",
                "Also treat these venues as predatory. They count as predatory for this "
                "run in addition to everything on the lists chosen above. Leave empty "
                "to use those lists as they stand.",
                "Empty — the chosen lists are used as they stand.",
                # Crossref, not DOAJ: a venue worth blacklisting is one no
                # directory of vetted journals will have, and the DOI registry
                # screens nobody. See _DIRECTORIES.
                source="crossref")
    return wl, bl


def _reference_lists_section():
    """Step 3's reference lists. Returns the selected list ids.

    Built from the registry rather than named here, so adding a catalogue is a
    registry entry plus its fetcher — nothing on this page mentions a particular
    list. Checkboxes rather than a radio, because the lists are cumulative: the
    question is "which catalogues does this run screen against", and answering it
    with several is the normal case, not an edge one. It is also what a methods
    section has to state — "we screened against X and Y" — which a single choice
    cannot express.

    Beall's List ships with the tool and is on by default; anything else has to
    be downloaded once in Settings, and is offered disabled until it is, with the
    reason on the control rather than as a failure at run time.
    """
    with _options_section("📚", "Predatory lists"):
        chosen = []
        cols = st.columns(max(2, len(reference_lists.ids())))
        for col, pid in zip(cols, reference_lists.ids()):
            with col:
                usable = reference_lists.usable(pid)
                state = reference_lists.status(pid)
                if usable:
                    n = state["entries"] or "—"
                    age = state["age_days"]
                    when = ("shipped with the tool" if reference_lists.is_vendored(pid)
                            else f"downloaded {age} day(s) ago" if age is not None
                            else "downloaded")
                    help_text = f"{n} entries, {when}. {reference_lists.home(pid)}"
                else:
                    help_text = ("Not on this computer yet. Its publisher grants no "
                                 "licence to redistribute it, so it is not shipped "
                                 "with the tool — download it once on the Settings "
                                 "page.")
                on = st.checkbox(
                    reference_lists.label(pid),
                    value=pid in reference_lists.default_ids() and usable,
                    key=f"list_{pid}", disabled=not usable, help=help_text)
                # No entry count here: the checkbox's own "?" already carries it,
                # and printed twice a pair of numbers reads as two different facts.
                # The missing-list warning stays — it says why the box is disabled.
                if not usable:
                    st.caption("⚠️ not downloaded")
                if on and usable:
                    chosen.append(pid)
        if not chosen:
            # An empty selection would match nothing and report every paper as
            # clean, which is the one failure this page must not let through
            # silently. Step 4's button is disabled while this holds.
            st.warning("Choose at least one list — with none selected there is "
                       "nothing to check the papers against.")
    return chosen


def _step_options(checker, llm):
    """Step 3 — everything about *this* run. Returns an :class:`_Options`."""
    llm_models = llm.models
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
        with _options_section("🧠", "LLM", "required"):
            provider_col, model_col = st.columns(2)
            provider = provider_col.selectbox(
                "LLM provider", llm_providers(), key="provider", on_change=_provider_changed,
                help="Which backend answers. Defaults to `.env`'s `LLM_PROVIDER`; choosing "
                     "another here changes this run only, and leaves `.env` alone. Each "
                     "provider needs its own credentials — the line under Step 1 says whether "
                     "the one selected has them.")
            # The configured names, and a way past them, in one control: the list
            # is what `.env` knows about, and anything else can be typed straight
            # into it.  A list alone was a dead end on a machine whose .env
            # happened not to name the wanted model, and a list plus a separate
            # "Model name" box asked one question twice — visibly so for every
            # provider with no configured names, where the list held nothing to
            # pick.  A typed name cannot be checked before the run, but "cannot
            # be verified" is not "must not be offered": an unverified name that
            # runs beats a verified one that is not the model that was meant.
            model = model_col.selectbox(
                "Model / deployment", llm_models, key="model",
                index=0 if llm_models else None,
                accept_new_options=True, placeholder="Choose or type a model name",
                help="Which model of that provider this run uses — **pick one, or type any "
                     "name the provider serves**. The listed names come from `.env`: "
                     "`AZURE_OPENAI_DEPLOYMENT` for the provider `.env` itself selects, "
                     "`LLM_MODELS_<PROVIDER>` for any other. A typed name is passed on "
                     "exactly as written (for Azure, that is the **deployment** name) and is "
                     "not checked until the first request. To have it offered here from now "
                     "on, add it to `.env` and restart — `.env` is read once, at startup.")
            model = (model or "").strip()

    lists = _reference_lists_section()

    with _options_section("🔗", "Crossref"):
        # One explanation, on the control.  It used to be said twice — a tooltip
        # and a caption below saying the same thing in the same words — which is
        # the most text this step could spend on its least contested setting.
        crossref = st.checkbox(
            "Cross-check every venue against Crossref",
            value=True,
            key="cr",
            help="**Recommended.** Crossref is the official registry of published articles, "
                 "looked up by each paper's DOI. When on (the default), the journal name and "
                 "ISSN that Semantic Scholar reports are confirmed against Crossref's own "
                 "record, and DOIs resolve matches the metadata alone leaves uncertain — "
                 "catching papers where Semantic Scholar has the wrong venue. It costs one "
                 "lookup per paper, and is skipped automatically when there is no internet. "
                 "Uncheck to force a fully offline run.")
    # Offered on every check.  They were hidden for Batch CSV because
    # batch_check.py took neither flag — a gap in that script rather than a
    # property of the check, and one that made a single run produce two files
    # that disagreed about the same papers.  The script takes both now.
    wl, bl = _venue_list_section()
    aux_lists = {"--whitelist": wl, "--blacklist": bl}
    # Both ride on extra_args, the same mechanism --crossref already uses, so
    # runners.py needs no new plumbing. cli_args() returns nothing when the
    # selection is the default, keeping an untouched Step 3 producing exactly the
    # command line it produced before this control existed.
    extra_args = ([] if crossref else ["--crossref", "off"]) + reference_lists.cli_args(lists)
    return _Options(crossref, lists, wl, bl, aux_lists, extra_args or None,
                    provider if checker.needs_llm else "",
                    model if checker.needs_llm else "")


def _step_run(checker, items, llm, options):
    """Step 4 — restate what is about to happen, run it, show what came back."""
    crossref, wl, bl = options.crossref, options.whitelist, options.blacklist
    lists = options.lists
    aux_lists, extra_args = options.aux_lists, options.extra_args
    provider, model = options.provider, options.model
    llm_ok, llm_msg = llm.ok, llm.message
    # Step 4 — run.  The summary restates every decision made above, each under
    # the name of the thing it decides, so what is about to run can be checked
    # without scrolling back up — and without having to work out what a bare
    # "Merged" or "8 paper(s)" was answering.
    step_header(4, "Run", "everything above, in one go",
                "ready" if items else "add papers first", "ok" if items else "wait",
                learn="Understanding the report")
    # The whitelist reads differently on the CSV, and the summary says so on the
    # hover: on a workbook it decides which papers are reported, on a CSV it also
    # decides how many rows there are — which is the one property scripts reading
    # that file lean on.
    wl_why = ("Only papers from these venues are checked, so the CSV has one row per "
              "*checked* record rather than one per record supplied."
              if checker.kind == "csv" and wl else "")
    summary = [
        ("Check", f"{checker.icon} {checker.title}", "", ""),
        # The summary is read immediately before pressing Run, which is the last
        # moment anyone can notice they are about to check the example instead of
        # their own corpus.
        ("Papers", (f"{len(items)} loaded — the example" if _is_example_corpus()
                    else f"{len(items)} loaded") if items else "none yet",
         "" if items else "bl-sum-todo",
         "The bundled example corpus, not your data — replace it in Step 2."
         if _is_example_corpus() else ""),
        ("Predatory lists",
         ", ".join(reference_lists.label(p) for p in lists) if lists else "none chosen",
         "" if lists else "bl-sum-off",
         "Which published lists this run screens against. A venue on any of them is "
         "flagged, and the report names which one."),
        ("Crossref", "On" if crossref else "Off", "" if crossref else "bl-sum-off", ""),
        ("Whitelist", f"{len(wl)} venue(s)" if wl else "—",
         "" if wl else "bl-sum-off", wl_why),
        ("Blacklist", f"{len(bl)} venue(s)" if bl else "—",
         "" if bl else "bl-sum-off", ""),
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

    def _cell(k, v, cls, why, i=0, n=1):
        # Cells in the right-hand half open their explanation leftwards; a panel
        # opening the other way runs past the edge of the page on a narrow window
        # and gets clipped.
        side = "end" if i >= n / 2 else "start"
        value = components.hint(v, why, cls=f"bl-sum-v {cls}", align=side)
        return f"<div><span class='bl-sum-k'>{k}</span>{value}</div>"

    # Keyed so that the screenshot script can wait on this summary — the one place
    # that states which check a press of Run would actually start.
    with st.container(key="runsummary"):
        st.markdown("<div class='bl-sum'>"
                    + "".join(_cell(*row, i=i, n=len(summary))
                              for i, row in enumerate(summary))
                    + "</div>", unsafe_allow_html=True)
    run = st.button("▶ Run check", type="primary", key="run",
                    disabled=items is None or not lists,
                    help=None if items is not None else "Add papers in Step 2 first.")
    if not items:
        st.caption("Add papers above to enable the button.")
    if run and items is not None:
        progress = st.progress(0.0, text="Starting…")
        log_line = st.empty()
        # Every check prints "[i/N] detail" as it works: the corpus pass counts
        # papers, the LLM pass counts venues.  The counter drives the bar and the
        # detail becomes its label, so the bar says what THIS check is counting
        # (and how long is left) rather than a noun hardcoded here for one of them.
        step = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]\s*(.*)")
        pulse = {"v": 0.0}

        def on_line(line):
            m = step.search(line)
            if m and int(m.group(2)):
                detail = m.group(3).strip()
                label = f"{m.group(1)}/{m.group(2)}"
                progress.progress(min(int(m.group(1)) / int(m.group(2)), 1.0),
                                  text=f"{label} · {detail}" if detail else label)
            else:                                    # no per-item counter — keep the bar alive
                pulse["v"] = (pulse["v"] + 0.05) % 0.9
                progress.progress(pulse["v"], text="Working…")
            if line.strip():
                log_line.caption("⏳ " + line[-140:])

        try:
            # What actually makes *this* check slow.  Every one of them scales
            # with the corpus; only the thorough check also waits on an LLM, and
            # naming a pass that is not going to run reads as a warning about
            # something that never happens.
            slow = ("large corpora and the LLM pass can take a while"
                    if checker.needs_llm else "large corpora can take a while")
            with st.spinner(f"{checker.title} running… {slow}."):
                if checker.kind == "csv":
                    res = runners.run_batch_csv(
                        items, crossref=crossref, aux_lists=aux_lists,
                        extra_args=reference_lists.cli_args(lists), on_line=on_line)
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
        notices.notify("Check complete ✅" if res.ok else "Check failed 🛑",
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
            results.render_csv_result(res)
        else:
            results.render_result(res, ran)

        # The machine-readable CSV, on demand, from a workbook result.  It cannot be
        # derived from the workbook: the batch check keeps every input record while
        # the workbook is deduplicated, so it is a second run of the deterministic
        # pass — offered here rather than done automatically, because that pass
        # repeats every Crossref lookup.
        if res.ok and ran.kind == "xlsx":
            st.markdown("**Also need it as a machine-readable list?**")
            st.caption("Runs the batch check over the same papers: `SS-ID, DOI, Status`, "
                       "**one row per checked record, in input order, with no "
                       "deduplication** — so it can have more rows than the workbook. Your "
                       "whitelist and blacklist apply here exactly as they did above; what "
                       "is not carried over is the thorough check's LLM verdict, which this "
                       "file has no column for. The rules pass, including its Crossref "
                       "lookups, runs a second time — the CSV cannot be built from the "
                       "workbook, because the workbook has already merged the repeats this "
                       "file is for.")
            if st.button("📄 Also produce the batch CSV", key="addon",
                         disabled=items is None or not lists):
                with st.spinner("Batch check running…"):
                    st.session_state["csv_addon"] = runners.run_batch_csv(
                        items, crossref=crossref, aux_lists=aux_lists,
                        extra_args=reference_lists.cli_args(lists))
            addon = st.session_state.get("csv_addon")
            if addon is not None:
                results.render_csv_result(addon)


def page_check():
    """The four-step flow, in the order the page renders it.

    Each step is its own function and hands the next what it needs as a return
    value. They used to be four stretches of one 381-line function sharing 29
    locals, which read top-to-bottom well enough but hid which of those locals
    genuinely crossed a step boundary — three of them, it turns out, and one
    comment already had to say so in prose.
    """
    # The old one-line docstring survived the split as a second string literal
    # below the new one — and Streamlit's magic renders a bare expression, so it
    # was being *printed at the top of the page*, above the title, on every
    # render. Caught by retaking the screenshots, which is what they are for.
    st.title("🕵️ Predatory venue check")
    st.caption("Is the paper's **journal or publisher on a predatory list** — Beall's, "
               "Kscien's, or your own? Add your papers as JSON, run, and download the "
               "Excel report. Step-by-step instructions are on the **Tutorial** page.")
    st.markdown(styles.PAGE_CSS, unsafe_allow_html=True)
    # The one pair of colours the stylesheet cannot work out for itself.
    st.markdown(styles.field_placeholder_css(), unsafe_allow_html=True)
    rail_slot = st.empty()          # filled once the steps below have run
    # Anything the previous render queued on its way out — see notices.notify_next_run.
    notices.flush_notices()

    if st.session_state.get("checker_id") not in runners.CHECKERS_BY_ID:
        st.session_state["checker_id"] = runners.CHECKERS[0].id
    checker = runners.CHECKERS_BY_ID[st.session_state["checker_id"]]
    # Step 3's provider starts at `.env`'s, not at whichever happens to head the
    # list.  Set here rather than there because Step 1's readiness line, drawn
    # first, has to be answering about the same one.
    st.session_state.setdefault("provider", ENV_PROVIDER)
    # Before Step 2 builds its widgets: what is assigned to a widget's key ahead
    # of the widget is what the widget opens holding.
    _seed_example_corpus()
    llm = _step_choose_check(checker)
    items, token, quality_slot = _step_provide_papers()
    rail_slot.markdown(
        step_rail(items and not _is_example_corpus(), st.session_state.get("res")),
        unsafe_allow_html=True)
    options = _step_options(checker, llm)
    # The data-quality panel goes into the place Step 2 kept for it, and it is
    # written here — after Step 3, before Step 4 — for two reasons that used to
    # pull in opposite directions.
    #
    # Not in Step 2's own place, because Streamlit paints elements in the order
    # the script produces them: the work this panel does once per corpus held up
    # everything below it, and the first render after an upload is the one where
    # Steps 3 and 4 have no previous version to stand in, so the page simply had
    # no third step until the panel finished.
    #
    # But not after Step 4 either, which is where it went next.  A *run* happens
    # inside Step 4, and anything the script has not reached yet is marked stale
    # and faded to a third of its brightness — so through a run of several
    # minutes this panel, the last thing on the page, sat visibly dimmed the
    # whole time with nothing wrong with it.
    #
    # Between them costs what the panel costs, on the first sight of a corpus
    # only: 0.2-0.3s to validate 18,000 records and ~1.5s to build the report
    # for them, once the per-record filesystem call was taken out of the
    # validator.  That is Step 4's summary and its button arriving a moment
    # later — not a missing step, and not a dimmed panel.
    if quality_slot is not None:
        results.show_input_quality(items, token, key="input", into=quality_slot)
    _step_run(checker, items, llm, options)
# ── Navigation ──────────────────────────────────────────────────────────────
# Three pages in the sidebar: the flow, the instructions for it, and the one
# thing there is to configure.
nav = st.navigation([
    # The default page is served from "/", so it is given no url_path of its own.
    st.Page(page_check, title="Run a check", icon="▶️", default=True),
    st.Page(tutorial_page.page_tutorial, title="Tutorial", icon="📘", url_path="tutorial"),
    st.Page(settings_page.page_settings, title="Settings", icon="⚙️", url_path="settings"),
])
sidebar()          # below the page links, on every page
nav.run()
