"""Step 3's venue boxes: type a venue, or pick one out of a directory.

One public function, :func:`venue_list_editor`, and the machinery behind it —
which is most of this file, because the box is the most worked-over control in
the app.  The two lists it builds are opposites (see ``user_lists.py``) and are
searched in different directories for that reason, but they are the *same
control*: same skeleton, same bands, same handlers, differing only in which
directory a keystroke reaches and what an empty list means.

The directories themselves are ``doaj.py`` and ``crossref.py``; the venue shape
they hand back and the local index they are searched through are
``venue_lookup.py``'s.  What is here is everything between those and the screen:
when to look something up, what to do with a click, and how to say what an entry
will be matched on.

Reads its geometry and its palette from ``styles``, so the dropdown it draws
inside the component's iframe lands exactly on the bands the page's own CSS sets
— the one number that has to mean the same thing on both sides of that boundary.
"""

import html
import json
import time

import streamlit as st

import sys
from pathlib import Path

# Flat imports across ui/'s feature folders — `import styles`, not
# `from ui.styles import styles`.  ui/ is not a package (Streamlit runs app.py as
# a script), so every folder is put on the path first.  Repeated per module
# rather than shared, so each still imports when it is the thing being run: from
# a shell, or by anything that never touched app.py.  Same shim the check
# engine's own foldered modules use — see src/bealls_list_check/*/.
_UI = Path(__file__).resolve().parent.parent
for _p in [_UI, *(d for d in _UI.iterdir() if d.is_dir() and any(d.glob("*.py")))]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import crossref
import doaj
import styles
import venue_lookup
from notices import notify_next_run

# Step 3's venue boxes search a directory while a name is being typed, which
# Streamlit's own widgets cannot do — they filter options client-side and call
# back only on Enter or blur.  Optional at import: a checkout whose venv predates
# this dependency still runs, with the boxes falling back to plain text entry and
# the lookup simply absent.
try:
    from streamlit_searchbox import st_searchbox
except Exception:
    st_searchbox = None


# ── Step 3's venue lists ─────────────────────────────────────────────────────
# A venue list is a *list*, and it is now edited as one: rows with a ✕, built by
# searching DOAJ or by typing, with the JSON it serialises to on the second tab
# of the same box.  It used to be a 90px textarea holding raw JSON, which made
# the format the interface — every add was text surgery, every removal was careful text
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
# every entry is still in force, and the JSON tab holds all of them.
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
    local = (doaj.have_local() if source == "doaj" else
             crossref.have_local() if source == "crossref" else False)
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
                                  "boxShadow": "inset 0 0 0 2px rgba(59,130,246,.45)"},
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
def doaj_search(term: str, limit: int):
    """Memoized :func:`doaj.search` — one request per distinct term per hour.

    Same reasoning as :func:`crossref_search` below: Streamlit reruns the whole
    script on every click anywhere on the page, and the prefixes of a word are
    re-sent as it is extended and backspaced.
    """
    return doaj.search(term, limit=limit)


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

    **All of a venue's ISSNs go in one pill**, not one pill each.  Nearly every
    journal has two, one registered for the print edition and one for the online
    one, so a directory hands back both — and two pills each reading *ISSN* look
    like two journals stuck together rather than one journal's two numbers, which
    is exactly the question they got asked.  One pill, and the tooltip says why
    there are two of them.
    """
    # One chip, for the one identifier this entry is matched on: the most precise
    # it carries (see match.user_entry_signal).  The row used to list every
    # identifier, which was right when every one of them was used and became a
    # promise the run does not keep once the precise one wins — "matches on ISSN
    # 0893-6080 · venue name" on an entry whose name will never be looked at.
    chips = []
    issns = list(entry.get("issns") or [])
    domain = entry.get("domain")
    if issns:
        why = ("The journal&#39;s ISSN. A paper is matched when its own ISSN is exactly "
               "this — the surest of the three, because an ISSN belongs to one journal "
               "and nothing else.")
        if len(issns) > 1:
            why += (" Journals usually hold two, one for the print edition and one for "
                    "the online one; both are kept here, and a paper carrying either is "
                    "matched.")
        # The word alone when the row is already headed by the number — but only
        # when that number is the whole of what this entry holds.  With two, the
        # pill lists both, because "ISSN" beside one of them would hide the other.
        label = ("ISSN" if issns == [shown] else
                 "ISSN " + " · ".join(html.escape(i) for i in issns))
        chips.append(f"<span class='bl-sig bl-sig-issn' title='{why}'>{label}</span>")
    if domain and not issns:
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
    if entry.get("name") and not issns and not domain:
        chips.append("<span class='bl-sig' title='The venue&#39;s name. A paper is matched "
                     "when this name appears as a whole word inside the paper&#39;s own "
                     "venue name — so a short or common name can also match journals you "
                     "did not mean. An entry that also carries an ISSN or a website is "
                     "matched on that instead, because a name is shared and those are "
                     "not.'>venue name</span>")
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
    if source == "doaj" and doaj.have_local():
        index, error = doaj_index()
        venues, total = ((), 0) if error else doaj.search_local(index, term, limit)
        pending = False
    elif source == "doaj":
        # No local copy: ask DOAJ. Same shape as the Crossref branch below, and
        # the same floor, because both are a round trip.
        result = doaj_search(term, limit)
        error, venues, total = result.error, result.venues, result.total
        pending = len(term) < doaj.MIN_TERM
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
        # What to do, not where the lookup happens.  This line used to read
        # "Start typing — DOAJ is searched on this computer as you go", which
        # answered a question nobody standing at an empty box is asking: whether
        # the directory is the downloaded copy or the API is a Settings matter,
        # and naming it here made the box's one line of guidance about the
        # plumbing.  The three things that can be typed are the useful part, and
        # they are the part that is not obvious.
        return (f"Type a journal name, website or ISSN — {directory['name']} matches "
                "appear as you type, or press Add to use exactly what you typed.")
    if last.get("pending"):
        floor = doaj.MIN_TERM if source == "doaj" else crossref.MIN_TERM
        return (f"Keep typing — {directory['name']} is searched from "
                f"{floor} letters, or press Add to use “{term}” as typed.")
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
                      + ("from a copy on this computer — no connection needed; "
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
                   f"All {len(venues)} are in force; the full list is on the "
                   "**JSON** tab.")


def venue_list_editor(label: str, key: str, about: str, empty: str,
                      *, source: str = ""):
    """One venue list, edited as a list. Returns the entries, or None if empty.

    The return value is the same JSON-shaped list the old text box parsed to, so
    everything downstream — the run summary, the temp file, the CLI flag — is
    unchanged.

    Both lists render the **same skeleton in the same order** — heading, one
    combobox with its Add button, a one-line note, then the list and its JSON as
    two tabs of one box — and every band in it has a height that does not depend
    on its contents.  That is what
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

    *about* is everything there is to say about the list — what it does and what
    leaving it empty means — and all of it lives on the ``?`` beside the label.
    It was on the page once: first as a longhand heading beside the word, then as
    a paragraph under it.  Either way the column's widest lines were spent
    restating, on every look, something read once and then known.  What is left
    on screen is the word and a mark to ask, which is the whole question the
    heading has to answer — *which of the two is this?* — and the mark answers
    the rest to whoever asks it.
    """
    st.session_state.setdefault(f"{key}_venues", [])
    st.session_state.setdefault(key, "")
    venues = st.session_state[f"{key}_venues"]

    # The heading is a band of its own, with a height that does not depend on
    # what it holds, so the two columns' add boxes share a row whatever either
    # list is called.
    # Focusable, and carrying its text as a label as well as a tooltip.  A `title`
    # alone is a mouse-only fact: it never appears for anyone tabbing through the
    # page, and it is announced inconsistently — which matters more here than
    # anywhere else on the page, because this mark now holds the *only*
    # explanation of what the list does.  `tabindex` puts it in the tab order and
    # `aria-label` gives a screen reader something to read out; a popover the
    # keyboard could open is the fuller answer, and is not one line of markup.
    mark = (f"<span class='bl-vl-q' tabindex='0' role='note' "
            f"aria-label='{html.escape(label, quote=True)}: {html.escape(about, quote=True)}' "
            f"title='{html.escape(about, quote=True)}'>?</span>"
            if about else "")
    st.markdown(f"<div class='bl-vl-head'><div class='bl-vl-label'>{label}{mark}</div></div>",
                unsafe_allow_html=True)

    # The note is written *above* the box it describes, in the place a field's
    # description goes, because that is what it mostly is: standing at an empty
    # box it says what can be typed, and it is wanted before the typing rather
    # than after it.  Once there is a term it reports what the directory found,
    # which reads no worse above the box than below it — the term is right there
    # either way.  It was under the box only because it was thought of as
    # feedback, and feedback is the smaller half of its job.
    #
    # The slot is reserved first and written after the box has run, because what
    # the note says depends on the keystroke the box just sent.  Always rendered,
    # always one line: a note that appeared only when it had something to say
    # would take everything below it up and down the page as the query changed.
    #
    # `st.container` and not `st.empty`, which is the obvious way to reserve a
    # slot and the wrong one here.  An empty slot is *cleared* before it is
    # written, so its contents are a new element every run: the browser threw the
    # note's node away and built another one on every interaction anywhere on the
    # page — identical text, visibly redrawn. A container's child is delivered in
    # the same position each run and diffed like any other element, so a note
    # that has not changed is left alone.  (Verified in the running app by
    # marking the node and toggling an unrelated widget: with `empty` the mark is
    # gone, with `container` it survives.)
    note_slot = st.container()
    note = _venue_input(key, source)
    with note_slot:
        st.markdown(f"<div class='bl-vl-note'>{html.escape(note)}</div>",
                    unsafe_allow_html=True)

    # The list and its JSON are two views of one thing, so they are two tabs of
    # one box rather than a box and a fold beneath it.  As a fold they read as
    # separate controls stacked in a column — the list, and then an unrelated
    # editor under it that happens to hold the same entries — and the column paid
    # for the fold's header whether or not anyone opened it.  Tabs say what is
    # true: same list, two ways of looking at it, one at a time.
    #
    # Both panes are rendered on every run (Streamlit hides the inactive one
    # client-side), so switching tabs costs no rerun and the JSON stays in sync
    # without being looked at.  They are given the same height for the same
    # reason every other band here has a fixed one: the column below must not
    # move when the tab changes.
    with st.container(key=f"venuetabs-{key}"):
        # Clear sits at the right-hand end of the tab line, on the one row this
        # box already spends on chrome, rather than under the JSON pane where it
        # was — reachable only from the tab that has least to do with it, and
        # costing a band of its own.  It is written before the tabs so it lands
        # ahead of them in the DOM and can be lifted onto their row by the
        # stylesheet; being out of flow, it adds no height to the column.
        with st.container(key=f"venueclear-{key}"):
            # Always rendered, disabled when there is nothing to clear: a button
            # that came and went would take its band's height with it.
            st.button("Clear the list", key=f"{key}_clear", on_click=_clear_venues,
                      args=(key,), disabled=not venues,
                      help="Remove every entry from this list.")
        rows_tab, json_tab = st.tabs(["List", "JSON"])
        with rows_tab:
            # A fixed height with its own scrollbar: the one part of this column
            # whose contents grow, given a size that does not.  Streamlit's
            # `height=` is used rather than a stylesheet rule so the scroll
            # behaviour is the container's own and survives a change to its
            # internals.
            with st.container(height=styles.VENUE_BOX_HEIGHT, border=True,
                              key=f"venuelist-{key}"):
                _venue_list_body(key, empty)
        with json_tab:
            st.text_area(
                "Venue list as JSON", key=key, height=styles.VENUE_BOX_HEIGHT,
                label_visibility="collapsed", on_change=_venue_json_edited, args=(key,),
                placeholder='[{"name": "IEEE", "domain": "ieee.org"}, "Nature"]',
                help='The same list, in the format the check reads: a JSON list of '
                     '`{"name", "domain", "issns"}` objects, or bare strings like `"Nature"` '
                     'to match by name alone. Paste one from a colleague or a previous run '
                     'and it appears as rows on the **List** tab. Edits land when the box '
                     'loses focus (or on Ctrl+Enter).')
            error = st.session_state.get(f"{key}_jsonerr")
            if error:
                st.error(error)

    return list(venues) or None
