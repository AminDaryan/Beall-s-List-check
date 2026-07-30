"""Messages that outlive the render that raised them.

Streamlit paints only when a run finishes, and a run that ends in ``st.rerun``
has its output discarded — so a toast raised just before one goes down with it.
Anything that acts and then reruns (choosing a venue, switching a directory on)
queues its confirmation here instead, and the next render flushes it.

Its own module because three of them need it and it needs nothing back, which is
what keeps the import graph a graph.
"""

import streamlit as st


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
