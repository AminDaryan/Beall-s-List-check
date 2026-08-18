"""The shared UI pieces every page of this app is built from.

One place for the small things that were being written by hand wherever they
were needed: a hover explanation, the mark that offers one, a progress ring.
Three pages had three spellings of the same tooltip, and the one on Settings
rendered as a bare "?" glyph beside Streamlit's own icon, so the same gesture
looked like two different controls.

Read ``ui/components/README.md`` before adding a component or changing one.  It
records what each piece is for, when NOT to reach for it, and the rules the set
as a whole obeys — so a later change extends this vocabulary instead of starting
a fourth one.

Import-safe: no Streamlit, no state.  Every function returns an HTML string for
``st.markdown(..., unsafe_allow_html=True)``, which keeps the components
testable on their own and usable from any page.
"""

import html
import re

# Streamlit's own help glyph, copied from what it renders for a widget's
# ``help=`` argument.  Ours has to be the same drawing: a heading and a table
# cell take no help= argument, so those marks are drawn by hand, and a hand-drawn
# question mark next to a built-in one reads as a different control rather than
# the same offer of detail.
HELP_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
    'focusable="false"><circle cx="12" cy="12" r="10"></circle>'
    '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>'
    '<line x1="12" y1="17" x2="12.01" y2="17"></line></svg>')


def tooltip(text):
    """The attributes that turn any element into a hover explanation.

    Returns ``class='bl-help' title='...'`` — the help cursor plus the browser's
    own tooltip.  Native rather than a custom popup on purpose: it is keyboard
    and screen-reader reachable, it never escapes the viewport, and it costs no
    JavaScript in a page Streamlit is already re-rendering.

    *text* is plain text.  Markup is not rendered inside a title attribute, so a
    caller holding HTML should pass it through :func:`plain` first.
    """
    return f"class='bl-help' title='{html.escape(text, quote=True)}'"


def hint(inner_html, text, *, cls="", align="start"):
    """Wrap something already on the page so hovering it explains it.

    A chip, a value in a summary line, a phrase in a table cell.  Opens the same
    ``.bl-tip`` panel :func:`help_icon` does, so one gesture has one appearance
    wherever it is used.

    Returns *inner_html* untouched when *text* is empty, so a caller need not
    branch.  Use :func:`help_icon` instead when there is nothing to hover — when
    the explanation belongs to a heading or a column rather than to a phrase.
    """
    classes = f"{cls} " if cls else ""
    if not text:
        return f"<span class='{cls}'>{inner_html}</span>" if cls else inner_html
    side = " bl-tip-end" if align == "end" else ""
    return (f"<span class='{classes}bl-help bl-tip-host' "
            f"aria-label='{html.escape(plain(text), quote=True)}'>{inner_html}"
            f"<span class='bl-tip{side}' role='tooltip'>"
            f"{html.escape(plain(text))}</span></span>")


def help_icon(text, *, label="", align="start"):
    """The "?" mark, opening the app's tooltip panel on *text*.

    For a heading, a column, or a field label: everything describing a thing
    rather than changing it goes here, so a page can say what it is for without
    a paragraph under every control.

    ONE per section.  Two of these within a few pixels are two invitations to the
    same explanation, and the reader has to try both to find out they are not.

    The panel is ``.bl-tip``, styled to Streamlit's own, NOT the browser's
    ``title`` attribute.  A title attribute is drawn by the operating system: it
    cannot be styled, so beside Streamlit's panel it read as a different control
    however identical the mark was; it cannot render markup, so prose written as
    HTML arrived in it as "<b>whitelist</b>"; and it does not appear for a
    keyboard at all.

    *text* is passed through :func:`plain` — the prose on these pages is written
    as HTML because that is how the page draws it, and a tooltip shows the
    characters it is given.

    *align* is ``"end"`` for a mark near the right edge of the page, where a
    panel opening rightwards would run off it.
    """
    spoken = f"{label}: {plain(text)}" if label else plain(text)
    side = " bl-tip-end" if align == "end" else ""
    return (f"<span class='bl-help bl-q' tabindex='0' role='note' "
            f"aria-label='{html.escape(spoken, quote=True)}'>{HELP_SVG}"
            f"<span class='bl-tip{side}' role='tooltip'>"
            f"{html.escape(plain(text))}</span></span>")


def progress_ring(fraction, label=""):
    """A ring filled to *fraction* with the percentage inside it.

    For work that belongs to ONE control — a row's download, a single card's
    refresh.  It is drawn at the size of a button so it can stand where that
    button was, leaving the layout alone while the work runs.  Reach for
    ``st.progress`` instead when the work belongs to the whole page.

    *fraction* is 0..1 and is clamped; *label* becomes the hover text, which is
    where the detail behind the number goes.
    """
    frac = max(0.0, min(1.0, fraction))
    radius = 15
    sweep = 2 * 3.14159 * radius
    return (
        f"<div class='bl-ring' title='{html.escape(label, quote=True)}'>"
        f"<svg viewBox='0 0 40 40'>"
        f"<circle cx='20' cy='20' r='{radius}' class='bl-ring-track'></circle>"
        f"<circle cx='20' cy='20' r='{radius}' class='bl-ring-arc' "
        f"stroke-dasharray='{sweep:.1f}' "
        f"stroke-dashoffset='{sweep * (1 - frac):.1f}'></circle>"
        f"<text x='20' y='20' class='bl-ring-pct'>{round(frac * 100)}%</text>"
        f"</svg></div>")


def plain(markup):
    """Tag-free, entity-free text, for a slot that renders neither.

    A tooltip shows the characters it is given, so a ``<b>`` or a ``&nbsp;`` in
    prose written as HTML would be read out literally.
    """
    return html.unescape(re.sub(r"<[^>]+>", "", markup or "")).replace("\xa0", " ")
