# UI components

The shared vocabulary every page of this app is built from. **Read this before
adding a component or changing one**, so a new piece of interface extends this
set instead of starting a rival one.

It exists because that is exactly what went wrong. Three pages had three
spellings of the same tooltip. The one on Settings rendered as a bare `?`
character next to Streamlit's own help icon, so a single gesture — *hover this
to learn more* — looked like two unrelated controls, and one of them looked like
a typo.

---

## The rules this set obeys

**One meaning, one mark.** A gesture gets one appearance across the whole app. If
a piece of interface already means something here, reuse it rather than drawing a
second version a few pixels away.

**Match the framework before inventing.** Streamlit draws help icons, buttons and
progress already. Where a component has to be hand-drawn — a heading and a table
cell take no `help=` argument — it copies Streamlit's own markup so the result is
indistinguishable. `HELP_SVG` is Streamlit's icon, lifted verbatim.

**Say the cost, not the mechanism.** The reader is a researcher deciding whether
to trust a verdict, not an operator of this tool. "About 0.5 s a search · needs a
connection" is useful; "queries the REST API" is not.

**Detail hides, decisions show.** What describes a thing goes behind a `?`. What
changes the answer stays on the page. A section carries **one** help mark; two
within a few pixels are two invitations to the same explanation.

**A component never blocks the page.** Work belonging to one row is reported in
that row. `progress_ring` replaces the control it belongs to and leaves the
layout alone; a page-wide bar for a single row's download is what made half a
table disappear.

**Plain text where markup does not render.** A `title` attribute shows the
characters it is given. Pass prose through `plain()` or a `<b>` is read aloud.

---

## The components

Everything returns an HTML string for `st.markdown(..., unsafe_allow_html=True)`.
The module holds no Streamlit import and no state, so each piece can be exercised
on its own.

### The panel

Both `hint()` and `help_icon()` open the same panel, `.bl-tip`, styled to the one
Streamlit renders for a widget's `help=` — 8px radius, 6px/12px padding, 14px on
a 22.4px line, its one-pixel shadow, its surface in both colour schemes. Those
numbers were measured off Streamlit's panel, not guessed.

It is deliberately **not** the browser's `title` attribute. A title is drawn by
the operating system: it cannot be styled, so beside Streamlit's own panel it
reads as a different control however identical the mark is; it cannot render
markup, so prose written as HTML arrives in it as `<b>whitelist</b>`; and it
never appears for a keyboard.

Text is passed through `plain()` inside both functions, so markup cannot leak
into a panel by accident.

Pass `align="end"` for a mark near the right edge of the page — the panel opens
rightwards by default and would otherwise run off it.

Keep the text to a few lines. Everything true of a thing is not what a tooltip is
for: the Settings panel once held a description, both prices, a long explanation
and the disk state, ran to twelve wrapped lines, and pushed the part a reader
came for past its own bottom edge.

### `tooltip(text) -> str`

The raw attributes for an element built entirely by hand. Prefer `hint()` or
`help_icon()`; reach for this only when neither shape fits.

### `hint(inner_html, text, *, cls="") -> str`

Wraps something that is already on the page so hovering it explains it — a chip,
a value in a summary line, a phrase in a table cell.

```python
components.hint("OMICS Publishing Group", "Matched by publisher website.",
                cls="bl-chip bl-warn")
```

Returns *inner_html* untouched when *text* is empty, so a caller need not branch.

**Use when** a visible phrase carries the meaning.
**Not when** there is nothing to hover — use `help_icon`.

### `help_icon(text, *, label="") -> str`

The `?` mark itself, for a heading, a column, or a field label where no phrase
can carry the note. Streamlit's own glyph at Streamlit's own 16px, so it is
indistinguishable from the icon a widget's `help=` renders a few pixels away.

```python
st.markdown(f"<div class='bl-dir-name'>DOAJ {components.help_icon(about)}</div>",
            unsafe_allow_html=True)

mark = components.help_icon(about, label="Whitelist")   # spoken as "Whitelist: ..."
```

**One per section.** Everything describing that section goes in this one string.

**This is the only `?` in the app.** There were two — this one, and a boxed grey
question mark on the venue-list labels — sitting within a few pixels of each
other and of Streamlit's own, so one gesture had three appearances. If a new
place needs a help mark, call this; do not draw another.

Reachable without a mouse: `tabindex` puts it in the tab order, `role="note"`
and `aria-label` give a screen reader something to announce, and `.bl-q` carries
a focus ring. A bare `title` is a mouse-only fact, which matters most exactly
where this mark holds the only explanation there is. Pass *label* when the text
alone would be announced as a sentence with no subject.

### `progress_ring(fraction, label="") -> str`

A ring filled to *fraction* (0–1, clamped) with the percentage inside, drawn at
button size so it can stand where a button was. *label* becomes the hover text.

```python
slot.markdown(components.progress_ring(0.4, "Fetching Beall's List…"),
              unsafe_allow_html=True)
```

**Use when** the work belongs to one control, and the page around it must not
move. **Not when** the work belongs to the whole page — that is `st.progress`.

Feed it a real fraction. Both fetchers and both long check passes print an
`[i/N]` token for exactly this; parse that rather than animating a guess.

### `plain(markup) -> str`

Tag-free, entity-free text for a slot that renders neither — a `title`
attribute, a Markdown link title.

---

## Styling

Class names live in `ui/styles/styles.py`, which is the single stylesheet:
`.bl-help`, `.bl-q`, `.bl-ring*`, `.bl-chip`, `.bl-th`, `.bl-td`. Colours come
from the CSS variables defined there (`--bl-fg`, `--bl-fg-muted`, `--bl-border`,
`--bl-accent`), never as literals, so both colour schemes keep working. The one
deliberate exception is the status chips, which carry the workbook's own fills
from `config.STATUS_FILL_COLORS` — those must match the file they describe, so
they are not re-toned for a dark background.

---

## Before you call a UI change done

Look at it. A DOM query saying "the title attribute is present" is not evidence
that the page reads well; both faults above passed that test and were caught by
eye. Playwright is in the venv:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": 1500, "height": 1100},
                   device_scale_factor=2, color_scheme="dark")
    p.goto(url, wait_until="networkidle"); p.wait_for_timeout(2500)
    p.screenshot(path=out, full_page=True); b.close()
```

Check both `color_scheme` values, and check the state *during* a slow action, not
only before and after it.
