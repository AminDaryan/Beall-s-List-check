"""The Settings page: where each venue directory is searched.

Two choices and nothing else.  Step 3's venue boxes find a journal while it is
being typed by looking it up in a public directory; this page decides whether
that lookup goes over the internet or reads a copy kept on this machine.

**Both are working states, and the control has to say so.**  This was a pair of
switches, and a switch has an off — so "off" read as *this box does not work*,
which is the one conclusion the page must not invite.  It is now a pair of named
options with their costs written beside them, because the question here is
*where*, never *whether*: nothing on this page can stop a venue box searching.

Each option carries its own trade in its caption, so the choice can be made
without flipping it to find out what the other one does, and the line underneath
reports the copy on disk — how many journals, how big, how old.  There is no
Remove and no Refresh: choosing "over the internet" leaves the file alone (so
choosing back costs no download), and a copy older than a fortnight is replaced
on its own.

Nothing is downloaded until "on this computer" is chosen — which is the point.  A
first run of this app should cost nothing it was not asked for.
"""

import re
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
import venue_lists
import components
import reference_lists
import venue_lookup
from notices import flush_notices, notify, notify_next_run


# The two answers.  They were spelled out as places — "Over the internet" and "On
# this computer" — so that neither could be read as the off of a switch, i.e. as
# the box being disabled.  That job is now done by the captions under them, which
# price both answers, and by the "?" beside each, so the options themselves can
# be the two words a reader is already scanning for.  Named here because the
# recovery message quotes them back.
ONLINE = "Online"
LOCAL = "Offline"


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
        hours = max(int(days * 24), 1)
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    whole = int(days)
    return "yesterday" if whole == 1 else f"{whole} days ago"


def _dot(*parts):
    """Join facts with the separator the status line uses throughout."""
    return " &nbsp;·&nbsp; ".join(p for p in parts if p)


def _usable(state):
    """Whether the copy on disk is one that can actually be searched.

    ``present`` alone is not enough: a truncated or half-written file is on disk
    and is not a copy of anything.  ``cache_status`` reports that as a journal
    count of -1, and this is the one place that distinction has to be made — it
    decides both what the line says and whether choosing the copy refetches it.
    """
    return bool(state["present"]) and state["journals"] >= 0


def _state_line(module, state, title):
    """What is true right now, as ``(text, css_class)``.

    Said as a fact rather than as a warning.  Searching a directory over the
    internet is not a fault and not a thing left undone — it is one of the two
    answers, and it is the one the app starts with — so it gets the quiet tone
    and not an amber one.  Amber is kept for the single case that really does
    need a hand: a file that is here and cannot be read.
    """
    if not module.held():
        kept = (f"the {_bytes_human(state['bytes'])} copy is still here, so going "
                "back costs no download" if state["present"] else
                "nothing is stored on this computer")
        return _dot(f"Searching {title} over the internet", kept), "bl-dir-absent"
    if not state["present"]:
        # Chosen, but the file is not there — a download that failed, or a cache
        # deleted by hand between one render and the next.  The box still works,
        # so the line says what it is doing rather than only what is missing.
        return (f"No copy here yet — {title} is being searched over the internet "
                "until one arrives"), "bl-dir-absent"
    if not _usable(state):
        # The advice has to be advice that works, which is why the choice below
        # tests _usable rather than "is there a file": a damaged copy is one this
        # page will replace, not one it will congratulate itself on already having.
        return (f"The copy of {title} on this computer could not be read — choose "
                f"“{ONLINE}”, then “{LOCAL}” again to download it afresh"), "bl-dir-broken"
    return _dot("Searching a copy on this computer",
                f"{state['journals']:,} journals",
                _bytes_human(state["bytes"]),
                f"downloaded {_age_human(state['age_days'])}"), "bl-dir-present"


def _host(url):
    """The bare host of a URL — what a reader recognises a source by.

    A full URL in a table cell is mostly punctuation, and the path adds nothing
    a name and a link do not already say.
    """
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url or "").split("/")[0])


def _directory(module, *, title, key, searched_by, about, online, local, help_text):
    """One directory, on one row: its name, where it is searched, and any work.

    Name, choice and progress sit side by side rather than stacked. Stacked, each
    directory owned a band of the page most of which was empty — a heading, two
    words, and a gap the height of both waiting for a download that happens once
    and takes seconds. On a row the block is as tall as the thing it holds.

    Everything describing the directory hangs off the one "?" beside its name;
    what a download is doing appears at the right of its own row and nowhere
    else; and anything that goes wrong is a toast, because a failure that pushes
    the page around is a second problem on top of the first.
    """
    on = module.held()
    state = module.status()
    line, _tone = _state_line(module, state, title)
    detail = " ".join([searched_by, about, help_text,
                       f"Online: {online}.", f"Offline: {local}.", line])

    # The name column is sized to the longer of the two names and no more, so
    # the options sit beside the title instead of across the page from it.
    # Aligned to the top, not the centre. Streamlit's radio group is 40px tall
    # while its labels occupy the top 24 — the rest is empty — so centring the
    # row put the name 16px below the options it belongs to.
    name_col, choice_col, work_col = st.columns([1.5, 4, 2], vertical_alignment="top")
    name_col.markdown(f"<div class='bl-dir-name'>{title} "
                      f"{components.help_icon(detail)}</div>",
                      unsafe_allow_html=True)
    with choice_col:
        # No captions under the options: Streamlit sizes a horizontal radio's
        # columns to them, so options priced in different numbers of words put
        # the second button at a different distance on each row. Both prices are
        # in the "?" instead, which leaves two words and a column that is straight.
        choice = st.radio(f"Where {title} is searched", [ONLINE, LOCAL],
                          index=1 if on else 0, horizontal=True, key=f"where_{key}",
                          label_visibility="collapsed")
    with work_col:
        slot = st.empty()

    if choice == LOCAL and not on:
        module.hold(True)
        if _usable(state):
            # Already here from a previous time it was chosen.  Going back to
            # the internet does not throw the file away, so this costs nothing.
            venue_lists.clear_directory_caches()
            notify_next_run(f"{title}: searching the copy already on this computer.",
                            icon="✅")
            st.rerun()
        _fetch_directory(module, title, slot)
    elif choice == ONLINE and on:
        module.hold(False)
        venue_lists.clear_directory_caches()
        notify_next_run(f"{title}: searching over the internet again. "
                        "The downloaded copy is kept.", icon="🌐")
        st.rerun()


def _fetch_directory(module, title, slot):
    """Download one directory's list into *slot*, then forget the old index.

    The ring is the app's one way of showing work that belongs to a single
    control — the same component the reference-list table uses for its own
    downloads — drawn where that row's own progress belongs and leaving the rest
    of the page still.

    A failure is a toast. It used to be an st.error block, which appeared under
    the row and pushed everything below it down, so a download that went wrong
    also moved the thing the reader was about to click. The copy on this computer
    is untouched either way, and the "?" beside the name says what is there.
    """
    slot.markdown(components.progress_ring(0.0, f"Downloading {title}…"),
                  unsafe_allow_html=True)
    try:
        def on_progress(fraction):
            # A server that sends no length gives None; the ring then holds at
            # zero rather than claiming a share of the work that is not known.
            slot.markdown(
                components.progress_ring(fraction or 0.0, f"Downloading {title}…"),
                unsafe_allow_html=True)

        journals, error = module.load_journals(refresh=True, progress=on_progress)
        slot.markdown(components.progress_ring(1.0, f"Reading {len(journals):,} journals…"),
                      unsafe_allow_html=True)
    except Exception as exc:                      # a download must not break the page
        journals, error = [], f"{type(exc).__name__}: {exc}"
    finally:
        slot.empty()
    venue_lists.clear_directory_caches()
    if error:
        notify(f"{title} could not be downloaded — still searching over the "
               f"internet. {error}", icon="⚠️")
    else:
        notify_next_run(f"{title}: {len(journals):,} journals are now on this computer.",
                        icon="✅")
        st.rerun()


def _reference_lists_block():
    """The published lists a run can screen against, and whether this computer
    holds them.

    Separate from the two directories above, and the difference is worth stating:
    a directory only helps you *name* a venue, while these decide what counts as
    predatory. A directory can be searched over the internet; a reference list has
    to be on this computer, because a run compares every paper against the whole
    list and asking a website once per paper would be thousands of requests.

    Built from the registry, so a list added later appears here with no change to
    this page.
    """
    st.subheader("Predatory lists")
    st.caption(
        "The published lists Step 3 can screen your papers against. **Beall's List "
        "ships with the tool**; anything else has to be downloaded once, because its "
        "publisher grants no licence to redistribute it. A downloaded list is a dated "
        "snapshot, so a run can be repeated and get the same answer — the workbook's "
        "Summary records which lists and which dates produced it.")

    # One row per list, under a header — a reader comparing two catalogues is
    # asking the same four questions of each, and stacked prose makes that a
    # scroll instead of a glance.
    # Sized to what each column holds: a sentence or two of description, against
    # short facts either side of it.
    widths = [1.8, 1.4, 4.4, 2.4, 1.3]
    head = st.columns(widths, vertical_alignment="bottom")
    for col, name in zip(head, ("List", "Source", "What it covers",
                                "On this computer", "")):
        col.markdown(f"<div class='bl-th'>{name}</div>", unsafe_allow_html=True)
    # One rule under the whole row, not one per cell. Per-cell borders left a
    # stray line hanging under the empty button column, which read as a mistake.
    st.markdown("<div class='bl-thrule'></div>", unsafe_allow_html=True)

    slots, pending = {}, None
    for pid in reference_lists.ids():
        state = reference_lists.status(pid)
        label = reference_lists.label(pid)
        vendored = reference_lists.is_vendored(pid)
        home = reference_lists.home(pid)
        row = st.columns(widths, vertical_alignment="top")

        row[0].markdown(f"<div class='bl-td bl-td-name'>{label}</div>",
                        unsafe_allow_html=True)
        row[1].markdown(
            f"<div class='bl-td'><a href='{home}' target='_blank' "
            f"rel='noopener'>{_host(home)}</a></div>", unsafe_allow_html=True)
        row[2].markdown(f"<div class='bl-td'>{reference_lists.description(pid)}</div>",
                        unsafe_allow_html=True)

        # The counts live on the hover, not in the cell. Spelled out they ran to
        # ten wrapped lines in a column sized for two, and the total repeated a
        # number the breakdown already adds up to.
        counted = " · ".join(f"{k.replace('_', ' ')}: {n:,}"
                             for k, n in sorted(state["counts"].items(),
                                                key=lambda kv: -kv[1]))
        tip = f"{state['entries']:,} entries" + (f" — {counted}" if counted else "")

        # Shipping with the tool and being updatable are different questions, and
        # answering only the first is what left Beall's List with no button —
        # reading as "this list cannot be updated" when its scraper had been in
        # the repository all along.
        if vendored:
            cell = f"<div class='bl-td bl-td-ok'>Ships with the tool {components.help_icon(tip)}</div>"
        elif state["error"]:
            cell = f"<div class='bl-td bl-td-warn'>⚠️ {state['error']}</div>"
        elif not state["held"]:
            cell = ("<div class='bl-td bl-td-warn'>Not here yet<br>"
                    "<span class='bl-dir-sub'>Step 3 offers it greyed out "
                    "until it is</span></div>")
        else:
            age = state["age_days"]
            when = "today" if age == 0 else f"{age} day(s) ago"
            cell = (f"<div class='bl-td bl-td-ok'>{_bytes_human(state['bytes'])} · "
                    f"downloaded {when} {components.help_icon(tip)}</div>")
        row[3].markdown(cell, unsafe_allow_html=True)

        if reference_lists.fetch_command(pid) is None:
            row[4].markdown("<div class='bl-td bl-dir-sub'>—</div>",
                            unsafe_allow_html=True)
            continue

        # A placeholder, so the ring can replace this one button later without
        # the row being redrawn.
        with row[4]:
            slot = st.empty()
        slots[pid] = slot
        with slot.container():
            if st.button("Download" if not (state["held"] or vendored) else "Update",
                         key=f"fetch_{pid}", width="stretch",
                         help=("One download, then kept on this computer."
                               if not (state["held"] or vendored) else
                               "Fetches the list again and replaces the copy here. "
                               "The one you have keeps working if it fails.")):
                pending = pid

    # The fetch runs AFTER every row is on the page, not in the middle of the
    # loop. Run inline, it blocked before the remaining rows were drawn: pressing
    # Update on the first list made the second list vanish for the length of the
    # download, so the page appeared to lose half the table.
    if pending:
        _run_fetch(pending, slots[pending])


def _run_fetch(pid, slot):
    """Fetch one list, showing progress in the ring where its button was."""
    label = reference_lists.label(pid)
    step = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]")   # the fetchers' progress token
    seen = []

    def on_line(line):
        seen.append(line)
        m = step.search(line)
        if m and int(m.group(2)):
            frac = min(int(m.group(1)) / int(m.group(2)), 1.0)
            slot.markdown(components.progress_ring(frac, line.strip()), unsafe_allow_html=True)

    slot.markdown(components.progress_ring(0.0, f"Fetching {label}…"), unsafe_allow_html=True)
    ok, output = reference_lists.fetch(pid, on_line=on_line)
    slot.markdown(components.progress_ring(1.0, f"{label}: done"), unsafe_allow_html=True)
    if ok:
        notify_next_run(f"{label} is ready to use in Step 3.", icon="✅")
        st.rerun()
    st.error(f"Could not fetch {label}. The list on this computer is unchanged.")
    st.code(output[-1500:] or "(no output)")


def page_settings():
    """Two choices: where each of Step 3's two venue directories is searched.

    The page exists because the default has to stay cheap.  Nothing here is
    downloaded until it is asked for, which is the whole point — a first run of
    this app should cost nothing it was not asked for.
    """
    st.markdown(styles.PAGE_CSS, unsafe_allow_html=True)
    st.markdown(styles.field_placeholder_css(), unsafe_allow_html=True)
    flush_notices()

    st.title("⚙️ Settings")
    st.caption(
        "Step 3's two venue boxes find a journal while you type by looking it up in a "
        "public directory. This page chooses **where each directory is searched** — over "
        "the internet, or from a copy downloaded to this computer. Both work; the copy "
        "is faster and needs no connection, the internet needs no download. **Nothing "
        "is downloaded until you ask for it here.**")
    st.caption("Neither directory takes any part in any verdict. They only help you "
               "*name* a venue — what counts as predatory is Beall's List and your own "
               "blacklist, and nothing else.")

    st.divider()

    # Its own heading, like Predatory lists below. Two named things sat under the
    # page title with nothing saying what they were, so the page read as one list
    # of settings rather than two sections answering different questions.
    st.subheader("Venue directories")
    st.caption("Where each of Step 3's two venue boxes looks a journal up while "
               "you type. Neither decides any verdict.")

    _directory(
        doaj, title="DOAJ", key="doaj",
        searched_by="Searched by Step 3's <b>whitelist</b> box "
                    "(<i>only check these venues</i>).",
        about="The Directory of Open Access Journals: about 23,000 journals that "
              "passed its editorial screening. Each has a name, an ISSN, a publisher "
              "and a website.",
        online="About 0.1 s a search · needs a connection · nothing stored",
        local="Instant, and no connection needed · one 25 MB download",
        help_text=(
            "Online, the box asks DOAJ's own search API over the internet — fast, but "
            "DOAJ can refuse a search when it is busy. Offline, the whole list is "
            "downloaded once (25 MB, a few seconds) and searched on this computer "
            "instead, with nothing to ask and nothing to refuse. "
            "Going back to online keeps the downloaded file, so choosing offline "
            "again is instant rather than another download."))

    # No rule between the two directories. They are two rows of one group now,
    # not two sections, and a divider between them was most of the empty band
    # each used to own. The rules around the group still separate it.
    _directory(
        crossref, title="Crossref", key="crossref",
        searched_by="Searched by Step 3's <b>blacklist</b> box "
                    "(<i>also treat these venues as predatory</i>).",
        about="The DOI registry: about 167,000 journals, screened by nobody — which is "
              "what a blacklist needs. All have a name, 82% an ISSN, almost all a "
              "publisher, and none a website.",
        online="About 0.5 s a search · needs a connection · starts at two letters",
        local="Instant, from the first letter · one 51 MB download",
        help_text=(
            "Online, the box asks Crossref's own search API over the internet, which "
            "is why it waits for a second letter — one letter costs a request and "
            "answers nothing useful. Offline, the whole journal list is downloaded "
            "once (51 MB, about twenty seconds) and searched on this computer "
            "instead, with no request to wait for and so no such floor. "
            "Going back to online keeps the downloaded file, so choosing offline "
            "again is instant rather than another download."))

    st.divider()
    _reference_lists_block()

    st.divider()

    st.caption(f"Downloaded copies are kept in `{venue_lookup.CACHE_DIR}` — ignored by "
               "git, safe to delete by hand, and replaced automatically once older than "
               "a fortnight.")
