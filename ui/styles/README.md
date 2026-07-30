# The palette, the stylesheets, the geometry

> Deep-dive companion to [`styles.py`](styles.py). The
> [web app's README](../README.md) gives the short version and links here.

## Colour

Hues come from the [Tailwind CSS palette](https://tailwindcss.com/docs/colors) —
neutrals from *slate*, the accent from *blue* — declared once as CSS variables in
`_TOKENS` and again as Streamlit's own theme in
[`.streamlit/config.toml`](../../.streamlit/config.toml), so the widgets Streamlit
paints and the ones the app paints belong to one palette.

The accent is **not** Streamlit's default red, and that is a correctness point
rather than a preference: red is a *verdict* here. `on_list` is red in the
workbook, in the status chips and in the result preview, so an accent that was
also red made the page read as "flagged" where it only meant "selected".

### Why nothing in `_TOKENS` branches on light versus dark

Because it cannot be made to branch correctly. Streamlit has a theme switcher
(**⚙ → System / Light / Dark**) that repaints the app *without* touching
`prefers-color-scheme`, and it exposes no attribute naming the active theme —
not on `<html>`, `<body>` or `stApp`. An earlier version keyed off the media
query, so anyone whose browser preferred dark and who then chose **Light** got
Streamlit's white page with this file's dark-theme text on it: light grey on
white. There is no signal that would have fixed it.

So the tokens are written not to need one:

| | How |
|---|---|
| Text | `currentColor`, thinned with `color-mix` — a fraction of whatever colour the active theme is already using, so each level keeps its ratio to a background this file never has to know |
| Surfaces, borders | translucent overlays of that same colour: they darken a light page and lighten a dark one, by construction |
| Semantic fills | a translucent wash of one hue with the theme's own text over it — the hue carries the meaning, the theme carries the contrast |
| Accent | a single **opaque** blue-500. Being opaque, the page behind it is irrelevant: the fill clears 3:1 against both white and slate-900, and slate-900 on the fill clears 4.8:1 either way |

One consequence worth knowing when editing: the thinned text colours are
fractions, so **they compound if nested**. 82% inside 82% is 67%. Apply them to
leaves, never to a container that holds another one.

Streamlit's own widgets need no such care — it knows which theme is active, so
the `[theme]` / `[theme.dark]` pair in `config.toml` is applied correctly however
the theme was chosen.

### Code blocks are always dark

Streamlit paints its syntax highlighting in the *same* colours whichever theme is
active, and against its light code background those land at **2.5:1 to 2.9:1** —
numbers, strings and JSON keys were the least readable text in the app, which
matters when the tutorial and the expected-format panel are mostly JSON.

Better colours alone cannot fix it: clearing 4.5:1 against white needs a
luminance at or below 0.183, and clearing it against slate-900 needs 0.228 or
above, so no single value does both — and the active theme is not knowable (see
above). Pinning the code background dark resolves it, after which one set of
light token colours serves both themes at 9:1 to 12:1.

### The exception: status colours

The **status** chips and the shaded Status cells of the CSV preview take the
workbook's canonical fills from `STATUS_FILL_COLORS`, so that the app and the
file it produces cannot disagree. Only the text laid over them is chosen here, by
relative luminance (`readable_on`), which is what keeps `error` — a deep red
among pale fills — legible.

### Verified, not asserted

Every text colour on both pages was swept for WCAG AA in both themes. The only
element below its threshold is the **disabled** Run button's label, which is
exempt (inactive controls) and is still lifted from Streamlit's 2.4:1 to 4.8:1 on
white by a flat slate-500 — flat because the colour it would otherwise take a
fraction of is Streamlit's own already-faded one.

A `_check_css_comments()` assertion runs at import. A stray `*/` in these
stylesheets raises nothing in a browser — it silently discards every rule after
it — and has twice collapsed Step 1's comparison table without a word in any log,
so the app now refuses to start on one instead.

## Pointer and hover

One duration and one curve for the whole app (`--bl-hover`, 120ms — under the
~150ms at which a transition stops reading as a response to the pointer and
starts reading as an animation of its own), so the controls Streamlit paints and
the ones this file paints answer at the same speed.

**The cursor.** Measured across the app, one control disagreed with the rest:
every part of a selectbox — the wrapper, the value, the arrow, the
`role="combobox"` input — computed `cursor: text`, while checkboxes, radios,
expander headers, buttons and the upload dropzone were already `pointer`. A caret
says *type here*; the control opens a menu. It is given the pointer the others
have. The trade is deliberate: Streamlit's selectbox is searchable, and `text` is
the honest cursor for a field that accepts typing. Typing still filters — a
cursor changes no behaviour — but with six providers and a handful of models,
being legible as a menu is worth more than advertising a filter nobody needs at
that length.

**The hover.** Buttons lift 1px onto a soft shadow and drop back on `:active`, so
a press still feels like a press; `:not(:disabled)` keeps the disabled Run button
out of it. Everything else — selectbox, text input, text area, dropzone — is a
field or a row rather than an object, and answers with its border going to
`--bl-accent-line` behind a 3px `--bl-accent-soft` ring instead. A 1px rise on a
full-width text area reads as the layout twitching. The expander header takes the
same wash. `prefers-reduced-motion` drops the movement only: the colour change
carries the same message without it.

> Verifying this in an automated browser is misleading. `getComputedStyle`
> reports a transitioning property's *current* value, and a pane that is not
> compositing frames never advances the transition past t=0 — so a working hover
> reads as no hover at all. Inject `* { transition: none !important }` first and
> the target values appear.
