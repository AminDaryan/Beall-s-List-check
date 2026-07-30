"""The Settings page: whether each venue directory is held on this machine.

Two switches and nothing else.  On means the journal list is here and searched
here, off means it is not; off removes it, and a copy older than a fortnight is
replaced on its own, so there is nothing left for a Remove or a Refresh to do.
The reasoning is on the info icon beside each switch rather than on the page,
because a settings page is read when something needs deciding and the decision is
the switch.

Nothing is downloaded until it is switched on — which is the point.  A first run
of this app should cost nothing it was not asked for.
"""

import streamlit as st

import crossref
import doaj
import styles
import venue_lists
import venue_lookup
from notices import flush_notices, notify_next_run


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
    on = module.held()
    want = st.toggle(f"**{title}** — searched by the {offered}", value=on,
                     key=f"hold_{key}", help=help_text)

    if want and not on:
        module.hold(True)
        if module.downloaded():
            # Already here from a previous time it was switched on. Switching off
            # does not throw the file away, so this costs nothing.
            venue_lists.clear_directory_caches()
            notify_next_run(f"{title}: searching the copy already here.", icon="✅")
            st.rerun()
        _fetch_directory(module, title)         # reruns when it lands
    elif on and not want:
        module.hold(False)
        venue_lists.clear_directory_caches()
        notify_next_run(f"{title}: searching online again.", icon="🌐")
        st.rerun()

    held = on and state["present"]
    if held:
        count = (f"{state['journals']:,} journals" if state["journals"] >= 0
                 else "unreadable — switch it off and on again")
        line = (f"{count} &nbsp;·&nbsp; {_bytes_human(state['bytes'])} stored "
                f"&nbsp;&nbsp;—&nbsp;&nbsp; {coverage}")
        tone = "bl-dir-present"
    else:
        extra = (" · copy kept, switching back on is instant"
                 if state["present"] else "")
        line, tone = off_state + extra, "bl-dir-absent"
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
    venue_lists.clear_directory_caches()
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
        off_state="Searching DOAJ online — about a tenth of a second a search, needs a connection",
        help_text=_para(
            "About 23,000 open-access journals that passed DOAJ's screening, each "
            "with a name, an ISSN, a publisher and a website.",

            "Off, the box asks DOAJ's API. On, it downloads the list once — 25 MB, "
            "a few seconds — and searches it here instead. Switching off keeps the "
            "file, so switching back on is instant."))

    _directory_row(
        crossref, title="Crossref", key="crossref", offered="**blacklist**",
        coverage="every one has a name, 82% an ISSN, almost all a publisher — no websites",
        off_state="Searching Crossref online — about half a second a search, "
                  "needs a connection, starts from two letters",
        help_text=_para(
            "About 167,000 journals from the DOI registry, screened by nobody, which "
            "is what a blacklist needs. Names and publishers throughout, an ISSN for "
            "82% — and no websites at all, so entries match on name and ISSN.",

            "Off, the box asks Crossref's API. On, it downloads the list once — "
            "51 MB, about twenty seconds — and searches it here instead. Switching "
            "off keeps the file, so switching back on is instant."))

    st.caption(f"Stored in `{venue_lookup.CACHE_DIR}` · ignored by git · safe to delete "
               "by hand · replaced automatically once older than a fortnight.")
