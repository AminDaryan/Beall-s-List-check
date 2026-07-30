# The Tutorial page

> Deep-dive companion to [`tutorial_page.py`](tutorial_page.py). The
> [web app's README](../README.md) gives the short version and links here.

## The Tutorial page

[`TUTORIAL.md`](TUTORIAL.md) is rendered here, so the walkthrough has one copy —
readable in the app and on GitHub. It is not rendered verbatim:

- **Installing and launching are left out.** The file fences them between
  `<!-- app:skip-start -->` and `<!-- app:skip-end -->`, and the app drops that
  block. Anyone reading the page has demonstrably already started the app, so it
  is the one section that cannot apply; it stays in the file for the GitHub
  reader, who has not. Because they are prerequisites rather than steps, they are
  also **unnumbered**, which is what lets the walkthrough still read 1 – 7 with
  them gone.
- **Section headings get the numbered badge** the flow's own steps carry, instead
  of leaving the number as the first character of a line of prose. The `id` is
  the slug Streamlit would have generated (`_slug`), so links into these sections
  — the ⓘs, and the file's own cross-references on GitHub — resolve to the same
  anchors.
- **A contents menu** sits above the first section — one full-width row per
  section, ruled like a table. It was a strip of chips, which is the wrong shape
  for navigation: a contents list is read down a column rather than across a
  wrapped line, the chips re-flowed at every window width, and each entry's
  target was only as big as its text. It lists **every** level-2 section, not
  just the numbered ones; *What this tool checks* and *Where to go next* are as
  jumpable as the seven steps between them, and they carry a dot in the number's
  place so the column still lines up. Sections inside the skipped block are
  excluded, so no row can point at a heading the app does not render.
- **Screenshots are plain `<img>` tags** served from [`static/`](../static), not
  `st.image` — which re-encodes a PNG as JPEG, visibly lossy on a picture of
  text. Each is centred, capped at 40rem, and is a link to itself enlarged:
  clicking opens a pure-CSS `:target` overlay. No script is involved, because
  Streamlit strips `<script>` out of markdown. The overlay's `z-index` clears
  Streamlit's own chrome (999990 for the header, 999991 for the sidebar) — below
  those it covered the page but left the header on top of it, and its close
  button unclickable.

  The overlay shows each capture at **1500px, the width it was taken at**, not at
  its 3000px pixel width. `shoot_screenshots.py` captures a 1500px viewport at
  `deviceScaleFactor=2`, so the file is a 1500px picture at 2×; displaying it at
  3000 was showing the app at 200% zoom, which is why opening one landed the
  reader inside a wall of letterforms. It is capped at the window as well, so the
  overlay scrolls down but never sideways — the way the page it is a picture of
  does.

  **Every surface closes it**: the image, the backdrop around it, and a labelled
  button in a fixed bar that also says so. Closing used to be that button alone,
  a 2.5rem `×` to be found on top of a screenshot that had covered the window,
  while the image under the pointer showed `cursor: zoom-out` and did nothing —
  the only click-to-close surface was the backdrop, and the image covered every
  pixel of it. The bar also links to the file itself in a new tab, which is the
  way *up* in scale: at 1:1 the browser's own image viewer has real zoom and a
  Back button, neither of which a `:target` overlay can offer.
- **The column is capped** at 58rem. In `wide` layout a line of prose otherwise
  ran the whole window.
