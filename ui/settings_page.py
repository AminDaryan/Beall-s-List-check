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
    held = state["present"]
    want = st.toggle(f"**{title}** — searched by the {offered}", value=held,
                     key=f"hold_{key}", help=help_text)

    if want and not held:
        _fetch_directory(module, title)         # reruns when it lands
    elif held and not want:
        module.delete()
        venue_lists.clear_directory_caches()
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
