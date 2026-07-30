"""Everything the page is painted with: the palette, the stylesheets, the geometry.

Three kinds of thing live here, and they are together because they are the same
kind of fact — how the app *looks*, stated once so nothing can disagree with it:

  * **the palette** (:data:`_TOKENS`), written not to branch on light versus dark,
    for the reason set out above it;
  * **the stylesheets** — what Streamlit's own arguments cannot reach, split into
    the chrome both pages share and the rules each page needs;
  * **the geometry** of Step 3's venue column, which is the one place a number has
    to mean the same thing on both sides of an iframe boundary: the CSS here sets
    the bands, and ``venue_lists.searchbox_styles`` sizes the dropdown to match
    them.  They were one file's private constants until the dropdown had to agree
    with them; naming them here is what stops the two drifting apart.

Streamlit-aware but page-free: it reads the active theme (which only Python can
ask — see :func:`theme_is_dark`) and emits CSS, and knows nothing about steps,
venues or runs.
"""

import re

import streamlit as st

# The height of the box the venues live in, in pixels.  Fixed, with its own
# scrollbar, because it is the only band in the column whose contents grow: a box
# that resized to fit would move the JSON fold below it, and — since the two
# lists sit side by side and fill independently — would leave the whitelist's
# controls level with a different part of the blacklist's after every add.
# Roughly six rows, which covers the great majority of lists without scrolling.
VENUE_BOX_HEIGHT = 190


# The height of the band the search box sits in, pinned rather than measured from
# what is inside it — and it has to be pinned, because what is inside it moves.
#
# The box is an absolutely positioned iframe, so it contributes no height at all;
# the band exists only because `style_absolute` leaves two empty blocks behind to
# reserve it.  But when the component is remounted — which is what clears the box
# after a venue is added — Streamlit shows a **loading skeleton** in the element
# container while the new iframe boots, and that skeleton *is* in flow.  Measured
# frame by frame: 48px band -> 92px for ~280ms -> 48px again, taking the note, the
# list and the JSON fold 44px down the page and back.  That is the input "going
# down and up" after a match is chosen.
#
# A container whose whole job is to reserve a fixed band must not be sized by its
# contents.  Pinned here, the skeleton overflows it silently and nothing below it
# moves.
VENUE_BAND = 48


# How far the open menu stands clear of whatever is under it.  The gap is a
# margin *below the list*, which grows the container the list sits in — and that
# container is white (see searchbox_styles), so the extra band is white unless
# something is painted over it.  What paints over it is a copy of the list's own
# shape, in the page's colour, offset down by exactly this much: it lands on the
# band and nowhere else.  A spread would have been simpler and was wrong — it
# reaches *upwards* too, and 26px of it covered the bottom of the field.
# In pixels, because it is given to react-select as a CSS value.
MENU_GAP = 20


# What is above the open menu inside the component: react-select's 38px control
# and the 8px margin it puts between the control and the menu.
MENU_OFFSET = 46


# The height of the open menu — **fixed**, rather than following the number of
# matches, and that is the whole point of it.
#
# The menu is an opaque overlay, and what it overlays is the venue list: a box of
# rows, each with a ✕ button.  Sized to its contents, its bottom edge lands
# wherever the match count happens to put it — and when that is *inside* a row,
# the part of the row below the edge stays on screen.  Measured: a two-line DOAJ
# match ended the overlay at y=479 while the first row's ✕ ran from 462 to 493,
# leaving the bottom 14px of a button sitting under the panel looking like part
# of it.  That is what "the text behind the dropdown appears on it" is.
#
# An edge that always lands in the same place cannot land inside a row, so the
# menu is given the exact height that puts it on the list box's bottom edge.  The
# bands between the two are named as they are laid out rather than as one number,
# so a change to any of them can be followed here:
#
#   3rem                 the two empty blocks `style_absolute` leaves behind to
#                        reserve the frame's space in the flow
#   1rem                 Streamlit's gap between elements
#   2.5rem               the note band (`.bl-vl-note`)
#   VENUE_BOX_HEIGHT    the list box itself
#
# less what sits above the panel and the gap below it.
#
# **In pixels, and it has to be.**  This value is applied inside the component's
# iframe, whose document has its own root font size — Bootstrap's 16px, which the
# page's does not change.  So `rem` here does not mean what `rem` means in the
# bands it is being matched against: written as `calc(6.5rem + …)` it stayed at
# 294px while a page at a 20px root font grew its bands to 320px, and the overlay
# fell 26px short.
#
# **Known limit.**  Two of the three bands above are still set in `rem` by
# Streamlit itself (the reserved band and the gap between elements), and they are
# not ours to pin — rules that set them are matched but lose to Streamlit's own,
# measured.  So a reader whose browser default font size is not 16px gets an
# overlay that is a little short of the list box (16px at a 20px root) or a little
# past it (12px at 13px root).  Short is the one that shows, as a sliver of the
# row underneath; at the default, which is what almost every reader has, it lands
# exactly.  Fixing it properly means owning those two bands, which is a larger
# change to how the column is laid out than this note is worth.
OVERLAY_REACH = VENUE_BAND + 16 + 40 + VENUE_BOX_HEIGHT   # band, gap, note, list


MENU_HEIGHT = f"{OVERLAY_REACH - MENU_OFFSET - MENU_GAP}px"


def theme_is_dark():
    """Whether the dark theme is the one on screen.

    Asked in Python because CSS cannot: Streamlit's ⚙ switcher repaints the app
    without touching ``prefers-color-scheme`` and leaves no attribute naming the
    active theme — see the note above ``_TOKENS``.  ``st.context.theme`` does
    report the theme *in force*, override included.  Guarded, falling back to the
    app's own default base on a Streamlit that lacks it.
    """
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return True


# The field's fill and edge, keyed by whether the dark theme is in force.  Named
# once because two places need the same two values and they must not drift: the
# component paints the real field with them, and the stylesheet paints the shape
# that stands in for it while the component is remounting (see the
# `st-key-venuebox-::before` rule).
FIELD_BG = {True: "#1e293b", False: "#f1f5f9"}      # slate-800 / slate-100


FIELD_EDGE = {True: "#475569", False: "#cbd5e1"}    # slate-600 / slate-300


def field_placeholder_css():
    """The two field colours, as CSS variables for the stylesheet to use.

    The shape that holds the field's place while the component remounts has to be
    the field's own colour, and that colour is only knowable in Python.  Emitted
    per render, so it follows the ⚙ switcher like everything else.
    """
    dark = theme_is_dark()
    return (f"<style>:root{{--bl-field-bg:{FIELD_BG[dark]};"
            f"--bl-field-edge:{FIELD_EDGE[dark]};}}</style>")


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
ROW_TINT_CSS = """
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

  /* One duration and one curve for every hover in the app, so that the controls
     Streamlit paints and the ones this file paints respond at the same speed.
     120ms is under the ~150ms at which a transition stops reading as a response
     to the pointer and starts reading as an animation of its own. */
  --bl-hover:120ms cubic-bezier(.2,0,.2,1);
  --bl-lift:0 2px 8px rgba(15,23,42,.18);

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

/* ── Pointer and hover, on everything that acts like a button ───────────────
   Measured across the app, one control disagreed with the rest: every part of
   a selectbox — the wrapper, the value, the arrow, the `role="combobox"` input
   — computed `cursor: text`, while checkboxes, radios, expander headers,
   buttons and the upload dropzone were already `pointer`.  A caret says "type
   here"; the control opens a menu.  It is given the pointer the rest have.

   That does cost something and the trade is deliberate: Streamlit's selectbox
   is searchable, and `text` is the honest cursor for a field that accepts
   typing.  Typing still filters — a cursor changes no behaviour — but with six
   providers and a handful of models, being legible as a menu is worth more than
   advertising a filter nobody needs at that length. */
[data-testid="stSelectbox"] div[data-baseweb="select"],
[data-testid="stSelectbox"] div[data-baseweb="select"] *,
[data-testid="stSelectbox"] div[data-baseweb="select"] input {cursor:pointer !important;}

/* The hover itself.  Buttons lift; everything else, being a field or a row
   rather than an object, answers with its border and a soft ring in the accent
   instead — a 1px rise on a full-width text area reads as the layout twitching.
   :active drops the lift so a press still feels like a press. */
button[data-testid^="stBaseButton"] {
    transition:transform var(--bl-hover), box-shadow var(--bl-hover),
               background-color var(--bl-hover), border-color var(--bl-hover),
               color var(--bl-hover);}
button[data-testid^="stBaseButton"]:hover:not(:disabled) {
    transform:translateY(-1px); box-shadow:var(--bl-lift);}
button[data-testid^="stBaseButton"]:active:not(:disabled) {
    transform:translateY(0); box-shadow:none;}

/* The ring is drawn on the control Streamlit renders inside each widget, not on
   the widget wrapper, so it follows the rounded corners of the thing itself. */
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stTextArea"] div[data-baseweb="textarea"],
[data-testid="stFileUploaderDropzone"] {
    transition:border-color var(--bl-hover), box-shadow var(--bl-hover),
               background-color var(--bl-hover);}
[data-testid="stSelectbox"]:hover div[data-baseweb="select"] > div,
[data-testid="stTextInput"]:hover div[data-baseweb="input"],
[data-testid="stTextArea"]:hover div[data-baseweb="textarea"],
[data-testid="stFileUploaderDropzone"]:hover {
    border-color:var(--bl-accent-line);
    box-shadow:0 0 0 3px var(--bl-accent-soft);}

/* Rows: the expander header, and the label a checkbox or radio shares with its
   box.  A wash, because these are strips of the page rather than objects on it. */
[data-testid="stExpander"] summary,
[data-testid="stCheckbox"] label, [data-testid="stRadio"] label {
    transition:background-color var(--bl-hover), color var(--bl-hover);}
[data-testid="stExpander"] summary:hover {background:var(--bl-accent-soft);}

/* Motion is the decoration here; the colour change carries the same message
   without it, so only the movement is dropped. */
@media (prefers-reduced-motion:reduce) {
  button[data-testid^="stBaseButton"]:hover:not(:disabled) {transform:none;}
}
"""


# The rest of the page's styling, emitted once per render.  What is here is what
# Streamlit's own arguments cannot reach: the step headers, the comparison grid,
# the run summary, the upload dropzone, and one correction to Streamlit itself.
PAGE_CSS = "<style>" + _TOKENS + _CHROME_CSS + """
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

/* ── Step 3's venue lists ──────────────────────────────────────────────────
   A row per venue, and on each row a pill per identifier it carries.  The pills
   are smaller and squarer than .bl-chip: those are labels on a comparison table
   and want to be read, these are identifiers stacked several to a line and want
   to be scanned.  Monospaced digits, because most of them are ISSNs and a
   column of proportional figures does not line up. */
/* The two columns are aligned band by band rather than by luck: each band below
   is given a height that does not depend on what is in it, so the whitelist's
   controls sit on the same rows as the blacklist's however either is filled.
   `min-height` on the heading is what stops a three-line hint in one column and
   a two-line hint in the other from offsetting every control beneath them. */
.bl-vl-head {min-height:5.4rem;}
.bl-vl-label {font-size:.95rem; font-weight:600; color:var(--bl-fg); margin-bottom:.1rem;}
.bl-vl-hint {font-size:.8rem; line-height:1.45; color:var(--bl-fg-faint);}
.bl-vl-hint code {font-size:.92em; padding:1px 4px; border-radius:4px;
                  background:color-mix(in srgb, currentColor 13%, transparent);}
/* ── The venue box, and the Add inside it ──────────────────────────────────
   Add is not a second control beside the box, it is the box's own button: it
   acts on the text in the box and on nothing else, so it belongs at the right
   hand end of it rather than in a column of its own taking a quarter of the
   width away from the thing being typed into.

   It has to be overlaid rather than placed, because the input is inside the
   component's iframe and no Streamlit widget can be rendered in there. So the
   container is the positioning context, the iframe fills it, and the button is
   lifted out of flow onto the right hand end. `z-index` clears the iframe's own
   10 (set by style_absolute), and the button is only ~64px wide, so the rest of
   the field is still there to click into and type. */
/* The open menu is an overlay, and it has to win against everything that comes
   after it in the column: the note, the list box, the JSON fold. `style_absolute`
   gives the iframe `z-index: 10`, which is enough *within one stacking context* —
   but Streamlit wraps every element in a layout wrapper of its own, and a z-index
   only competes inside the context it is in. Where that traps it, the note and
   the list's own text paint straight over an open dropdown; reported and
   photographed against a browser that sorts them that way, while Chromium hit-
   tests the iframe on top and shows nothing wrong. Lifting the box *and* the
   wrapper Streamlit puts it in takes the whole subtree — iframe and Add button
   with it — above its siblings whatever any of them do, because they are
   siblings in one block and this one is now the higher of the two.

   The `:has()` rule degrades safely: a browser without it drops that selector
   and keeps the container's own lift. */
div[data-testid="stLayoutWrapper"]:has(> div[class*="st-key-venuebox-"]),
div[class*="stLayoutWrapper"]:has(> div[class*="st-key-venuebox-"]) {
    position:relative; z-index:30;}
div[class*="st-key-venuebox-"] {position:relative; z-index:30; --bl-venuefield:38px;}
div[class*="st-key-venuebox-"] iframe {width:100% !important;}
/* Exactly ONE box in the chain is positioned. Streamlit nests the button inside
   an element container, and absolutely positioning both stacked their offsets:
   5px + 5px put a 28px button 10px down a 38px row, which is 5px below centre.
   The container is placed; everything inside it goes back to being in flow.

   The container is then given the input's own height and told to centre what is
   in it, rather than being offset by a hand-computed top. A `top` in pixels is
   only correct while the button is exactly 28px and the field exactly 38px --
   one font size, one zoom level, one Streamlit version -- and it goes visibly
   off-centre the moment either changes. Centring in the band is right whatever
   height the button turns out to be. `--bl-venuefield` is declared above with
   the container's own `position`, because it is the same fact about the same
   element: the height react-select gives the field this button sits in. */
div[class*="st-key-venuebox-"] [data-testid="stElementContainer"]:has(button) {
    position:absolute; top:0; right:6px; z-index:11; width:max-content;
    height:var(--bl-venuefield); display:flex; align-items:center;}
div[class*="st-key-venuebox-"] [data-testid="stButton"] {
    position:static; width:max-content;}
div[class*="st-key-venuebox-"] button {
    position:relative;                      /* for the spinner below */
    min-height:28px; height:28px; padding:0 12px; width:max-content;
    font-size:.76rem; font-weight:600; border-radius:6px;
    background:var(--bl-accent); color:var(--bl-on-accent); border:0;}

/* ── Add turns into a spinner while a search is out ────────────────────────
   Typing into the blacklist costs a round trip to Crossref -- ~0.55s, behind a
   300ms debounce -- and for every bit of it the page still shows the frame from
   before the keystroke, with Add looking as ready as ever. Nothing had said that
   the box was busy.

   This is done in CSS off Streamlit's own script-state marker because that is the
   only signal there is. The wait happens *inside one blocking call*: the
   component hands the term to Python, Python queries, sets the options and
   reruns, and Streamlit paints only when a run finishes -- so there is no frame
   in between for Python to draw a spinner into, and a run that ends in a rerun
   has its output discarded anyway (which is why the "Added X" toast has to be
   queued; see notify_next_run).

   It cannot be scoped to *which* box is busy: neither `:focus-within` nor
   `iframe:focus` crosses into the component's frame -- measured, both false while
   the input inside it holds the caret. The scope that is left is tighter than it
   looks, because Add is only rendered at all while a box holds a term nobody has
   committed yet, which is the same situation as "I am searching". A rerun with
   both boxes empty has no button to spin.

   While it is out, the button is not a button dressed as one: its fill goes with
   its label, so what is left in the field is a spinner and nothing else. Only the
   *box* it occupies stays, at 47x28, so the field's contents do not shuffle when
   it comes back. The ring is drawn in the page's own border and accent rather
   than in the accent's foreground, because it is now sitting on the field
   instead of on a blue fill. */
[data-test-script-state="running"] div[class*="st-key-venuebox-"] button {
    pointer-events:none;
    background:transparent !important; box-shadow:none !important;
    filter:none !important;}
[data-test-script-state="running"] div[class*="st-key-venuebox-"] button > div {
    visibility:hidden;}
[data-test-script-state="running"] div[class*="st-key-venuebox-"] button::after {
    content:""; position:absolute; inset:0; margin:auto;
    width:15px; height:15px; border-radius:50%; box-sizing:border-box;
    border:2px solid var(--bl-border); border-top-color:var(--bl-accent);
    animation:bl-spin .6s linear infinite;}
@keyframes bl-spin {to {transform:rotate(360deg);}}
/* The spin is decoration; the "not now" is carried by the button losing its
   label, its fill and its pointer, which anyone who has asked for less motion
   still gets. */
@media (prefers-reduced-motion: reduce) {
  [data-test-script-state="running"] div[class*="st-key-venuebox-"] button::after {
      animation:none; border-color:var(--bl-border);}
}

/* ── The field's own outline, so it cannot blink out ───────────────────────
   Choosing a match remounts the component, and for the ~280ms the new iframe
   takes to boot there is no field on screen at all: the iframe exists but stands
   0px tall (measured), so the box simply vanished and came back. Hiding
   Streamlit's loading skeleton stopped that gap from *moving* anything, and by
   removing the grey placeholder it made the gap plainer to see.

   So the band paints the field's own shape itself. It sits under the iframe --
   which is positioned, and so above it -- and is invisible for as long as there
   is an iframe to cover it; while there is not, it is what stays on screen. The
   two colours come from Python, because the component's field is coloured from
   `st.context.theme` and CSS cannot ask which theme is in force (see _TOKENS);
   emitted per render by `field_placeholder_css`, they match it exactly. */
div[class*="st-key-venuebox-"]::before {
    content:""; position:absolute; top:0; left:0; right:0;
    height:var(--bl-venuefield); box-sizing:border-box; border-radius:4px;
    background:var(--bl-field-bg); border:1px solid var(--bl-field-edge);}
/* Out of flow, the button's container has no width to give its contents, and
   Streamlit's label is a flex child that will happily shrink to one character
   per line -- measured at 8px wide and 67px tall inside a 28px button. Every
   level from the button down is told not to wrap, and to size to its text. */
div[class*="st-key-venuebox-"] button,
div[class*="st-key-venuebox-"] button * {
    white-space:nowrap; overflow:visible; text-overflow:clip;}
div[class*="st-key-venuebox-"] button > div,
div[class*="st-key-venuebox-"] button > div * {width:auto; flex:0 0 auto;}
div[class*="st-key-venuebox-"] button:hover:not(:disabled) {
    filter:brightness(1.08); transform:none; box-shadow:none;}
/* The two empty markdown blocks style_absolute adds to reserve the iframe's
   space carry Streamlit's own block gap, which would leave a hole under a box
   that is only 38px tall. */
div[class*="st-key-venuebox-"] [data-testid="stMarkdownContainer"] {margin:0;}

/* The line under the combobox: what the directory said, or how to use the box.
   Fixed at two lines' worth and clipped, so the longest message DOAJ can return
   — a network error — occupies exactly what the shortest does, and the list
   below never moves.  The full text is on the control's own tooltip. */
/* 40px rather than 2.5rem, and the same for the two bands around it: the open
   dropdown has to end exactly on the venue list's bottom edge, and its height is
   set *inside* the component's iframe, where `rem` is the iframe's own root font
   size and not the page's. A page at a 20px root font grew these bands by 26px
   while the overlay stayed put, and the overlay fell 26px short — measured. Held
   to pixels on both sides, they cannot drift apart. */
.bl-vl-note {min-height:40px; max-height:40px; overflow:hidden;
             font-size:.78rem; line-height:1.25rem; color:var(--bl-fg-faint);
             padding-top:.3rem;}
.bl-vl-row {display:flex; align-items:center; gap:.4rem; flex-wrap:wrap;
            padding:.1rem 0; line-height:1.5;}
.bl-vl-name {font-size:.9rem; font-weight:600; color:var(--bl-fg);
             overflow-wrap:anywhere;}
.bl-vl-meta {flex-basis:100%; font-size:.74rem; color:var(--bl-fg-faint);}
/* What introduces the pills, and what makes them legible: a pill reading "name"
   says nothing by itself, and reads as a statement of intent after this. Not a
   pill itself — it is the label on the row, so it carries no fill and no border
   and does not compete with the identifiers it introduces. */
.bl-vl-on {font-size:.7rem; font-weight:600; letter-spacing:.04em;
           text-transform:uppercase; color:var(--bl-fg-faint);
           white-space:nowrap; margin-right:.05rem;}
.bl-sig {display:inline-block; padding:1px 7px; border-radius:6px;
         font-size:.72rem; font-weight:600; line-height:1.5;
         font-variant-numeric:tabular-nums; white-space:nowrap;
         background:var(--bl-mute-bg); color:var(--bl-fg-muted); cursor:help;}
.bl-sig-issn {background:var(--bl-ok-bg); color:var(--bl-fg);}
.bl-sig-site {background:var(--bl-accent-soft); color:var(--bl-fg);}
/* The one that means "this covers more than the journal named beside it".  Amber
   is the page's existing "look at this before you rely on it" — the same wash
   Step 4 puts behind an unverified model — and not red, which is a verdict. */
.bl-sig-wide {background:var(--bl-warn-bg); color:var(--bl-fg);}
/* ── The Settings page's directory panels ──────────────────────────────────
   One status line per directory, stated as a fact rather than as a warning: a
   missing local copy is a choice the page is offering, not a fault. So "not
   downloaded" is the muted tone and a present copy is the affirmative one --
   neither is amber, because nothing here is wrong either way. */
.bl-dir-state {display:inline-block; padding:3px 11px; border-radius:8px;
               font-size:.82rem; font-weight:600; line-height:1.5;
               font-variant-numeric:tabular-nums; margin:.15rem 0 .25rem;}
.bl-dir-present {background:var(--bl-ok-bg); color:var(--bl-fg);}
.bl-dir-absent {background:var(--bl-mute-bg); color:var(--bl-fg-muted);}
/* The switch and its one line of fact read as one thing, so the line is pulled
   up under the label rather than sitting a full block gap below it. */
.bl-dir-state {margin-top:-.35rem;}
.bl-vl-empty {font-size:.85rem; color:var(--bl-fg-faint); font-style:italic;
              padding:.15rem 0 .3rem;}

/* Streamlit puts 1rem between the elements of a vertical block, which is right
   between the parts of a form and much too much between the rows of a list --
   eight venues came to a column of text separated by whitespace rather than to
   a list.  Tightened only inside these containers, and only for the rows. */
div[class*="st-key-venuelist-"] div[data-testid="stVerticalBlock"] {gap:.2rem;}
div[class*="st-key-venuelist-"] [data-testid="stMarkdownContainer"] {margin-bottom:0;}
/* The box holds a scrollbar, so it needs the room for one and a quieter border
   than a bordered container's default -- it is a well the rows sit in, not a
   panel competing with the step's own frame. */
div[class*="st-key-venuelist-"] {border-color:var(--bl-border) !important;
                                 padding:.4rem .5rem !important;}
/* The ✕ next to each row: square, quiet, and the row's height rather than a
   button's, so a list of them reads as a column of rows and not as a keypad. */
div[class*="st-key-venuelist-"] button[data-testid^="stBaseButton"] {
    min-height:1.9rem; padding:0 .3rem; color:var(--bl-fg-faint);}
div[class*="st-key-venuelist-"] button[data-testid^="stBaseButton"]:hover:not(:disabled) {
    color:var(--bl-fg); border-color:var(--bl-accent-line);}

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
""" + f"""
/* ── The search box's band, pinned ─────────────────────────────────────────
   See VENUE_BAND. The box itself is an absolutely positioned iframe and adds
   no height; this container only reserves the row. When the component is
   remounted -- which is how the box clears after a venue is added -- Streamlit
   puts a **loading skeleton** in its element container while the new iframe
   boots, and that skeleton is in flow. Measured frame by frame: the band goes
   48px -> 92px for ~280ms -> 48px, carrying the note, the list and the JSON fold
   44px down the page and back. That is the input "going down and up" after a
   match is chosen.

   Two rules, because there were two faults:

   `min-height` first. Pinning the height alone did nothing, and the reason is
   worth writing down: this container is a flex item, so its `min-height: auto`
   resolves to the automatic *minimum content size* -- which outranks any height
   you give it. Measured: `height:48px !important` computed to 48px while the box
   still stood 92px tall. Zeroing the minimum is what lets the pin hold, and the
   skeleton then overflows the band silently instead of pushing it open.
   (`!important` on both because Streamlit's own `height:auto` for a vertical
   block is a class selector emitted after this stylesheet, and ties go to the
   later rule.)

   Then the skeleton itself. Contained, it no longer moves anything -- but it
   would still flash a 44px grey placeholder across the note. It is not telling
   anyone anything: the space is already reserved, and what is loading is a box
   that was on screen a moment ago. */
div[class*="st-key-venuebox-"] {{
    height:{VENUE_BAND}px !important; min-height:0 !important;}}
div[class*="st-key-venuebox-"] [data-testid="stSkeleton"] {{display:none !important;}}
</style>
"""


# The Tutorial page is long-form prose with full-width screenshots, and the app
# runs in Streamlit's `wide` layout.  Left alone, a line of text ran the whole
# window and a 3000px screenshot was scaled up to fill it, which made the images
# shout over the words they illustrate.  Both are capped here: the column to a
# comfortable measure, the images to something nearer their natural size, with
# a frame so a screenshot of a page reads as a picture of a page.
TUTORIAL_CSS = "<style>" + _TOKENS + _CHROME_CSS + """
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


def check_css_comments():
    """Fail loudly on an unbalanced ``/* */`` in any stylesheet above.

    A stray ``*/`` does not raise anything — the browser silently discards the
    rules that follow it, and the page comes back looking like a stylesheet that
    was never written.  It has happened twice to the block comments in these
    literals, each time collapsing Step 1's comparison table without a word in
    any log, so the app refuses to start on one instead.
    """
    for name, css in (("_TOKENS", _TOKENS), ("_CHROME_CSS", _CHROME_CSS),
                      ("PAGE_CSS", PAGE_CSS), ("TUTORIAL_CSS", TUTORIAL_CSS),
                      ("ROW_TINT_CSS", ROW_TINT_CSS)):
        depth = 0
        for token in re.findall(r"/\*|\*/", css):
            depth += 1 if token == "/*" else -1
            if depth < 0 or depth > 1:
                raise AssertionError(f"{name}: unbalanced CSS comment ({token})")
        if depth:
            raise AssertionError(f"{name}: unterminated CSS comment")


check_css_comments()
