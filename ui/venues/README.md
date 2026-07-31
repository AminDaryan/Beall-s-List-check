# The venue boxes, and the two directories behind them

> Deep-dive companion to [`venue_lists.py`](venue_lists.py). The
> [web app's README](../README.md) gives the short version and links here.

### The venue lists are edited as lists

Each list is a column of rows with a ✕ on each, built by typing or by searching
DOAJ. The JSON it serialises to is the **JSON** tab of the same box, not a
fold beneath it: same list, two ways of looking at it, one at a time. Both panes
render on every run, so the JSON stays in sync without being opened and switching
tabs costs no rerun. The controls come first and the rows follow: the list is the
only part that grows, so entries appear inside a box whose position is fixed
rather than pushing the controls that created them down the page.

#### One control, and a layout that does not move

Each list is built with a **single search-as-you-type box** and an Add button.
It was three controls — a text box, a DOAJ search box and a results dropdown —
which was one question, *which venue*, asked three times, and left the answer
half-entered in one of them while another held something else.

Both columns render the **same skeleton in the same order** and every band has a
height that does not depend on its contents. Measured in the running app —
adding four venues, removing one, and running searches that match and that do
not — every band keeps the same offset and the same height, in both columns:

| Band | height | offset below the heading |
|---|---|---|
| heading (`.bl-vl-head`) | 27px (`min-height:1.7rem`) | 0 |
| note (`.bl-vl-note`) | 40px (fixed, clipped) | 27px |
| search box + Add | 48px | 67px |
| tab bar (**List** / **JSON**), with **Clear the list** at its right-hand end | 42px | 147px |
| list box (`st-key-venuelist-*`) | 190px (`st.container(height=…)`) | 205px |

The heading was 86px while it carried a paragraph of explanation under the
name — sized for the longer of the two lists, so the shorter one paid for it in
blank space. That paragraph is on the **?** now and the band is one line, which
is 59px per column back.

Four things buy that:

- **The list box is a fixed-height scrollable container.** `st.container(height=)`
  rather than a stylesheet rule, so the scrolling is the container's own.
- **The rows sit below the controls**, so the one part that grows cannot push the
  controls that created them down the page.
- **The heading and the note are fixed bands.** The note is the search's only
  feedback, and it is clipped at two lines so the longest thing DOAJ can return —
  a network error — occupies exactly what the shortest does. The full text is on
  the control's tooltip.
- **Nothing is conditionally rendered.** Both comboboxes, both notes, both tab
  bars and both **Clear the list** buttons are always present, Clear merely
  disabled when there is nothing to clear, because a control that comes and goes
  takes its band's height with it. Clear is absolutely positioned at the
  right-hand end of the tab row — a band the box already spends — rather than
  under the JSON pane, which is the tab with least to do with it and cost a band
  of its own. The tabs are the same bargain: both panes are
  built every run and the inactive one is hidden client-side, so the band below
  cannot move when the tab changes.

### Two directories, one for each list

Each box searches as the name is typed, but not the same catalogue — and the
reason is the one that makes the two lists opposites:

| List | Directory | Why that one |
|---|---|---|
| **whitelist** | [DOAJ](https://doaj.org) | Lists journals that *passed* an editorial screening — the venues someone narrowing a run is naming. |
| **blacklist** | [Crossref](https://www.crossref.org) | The DOI registry: a journal is in it because its publisher pays to register DOIs, which screens for nothing. A venue worth blacklisting is by definition **not** in a directory of vetted ones, so DOAJ cannot help name it — while registering DOIs is part of *looking* established, so the venues a blacklist is for are here. |

Both hand back the same [`Venue`](venue_lookup.py), so the box that offers it and
the entry it becomes never have to know which directory it came from. Choosing a
match adds it with its official title and ISSN — choosing *is* adding, so there
is no button beside each result. **Add** takes the text verbatim instead,
whether or not the directory knows it.

#### DOAJ: the whole list, downloaded once

DOAJ's search API **can** be called per keystroke, and out of the box it is. That
claim used to read the other way here — that four letters earned a Cloudflare
**502 with `Retry-After: 60`** — and it was too strong. Measured both ways: three
bursts, thirteen requests with no gap between them, answered 200 in 0.05–0.34 s
with no 502 anywhere; and then the app, driving it normally an hour later, took a
502 on an ordinary two-word query. So the API is the default, its failure says so
plainly instead of looking like "no match", and the message names the choice that
makes it stop happening.

The download is the alternative, not the requirement. DOAJ publishes the entire
journal list as one CSV, so [`doaj.py`](doaj.py) can fetch it **once**
(~23,000 journals, ~25 MB), reduce it to the fields a venue entry needs, and
cache it in `ui/venues/.cache/` (gitignored, refetched after a fortnight).
Everything after that is local:

| | |
|---|---|
| First load | ~25 s, once per machine per fortnight |
| Cached load | ~0.1 s |
| One search | 30-90 ms over 23,000 journals |

So the box keeps up with typing, works offline once primed, and puts no load on
DOAJ at all. The trade is that a journal added to DOAJ since the last fetch has
to be typed by hand — which the box allows anyway.

Ranking is by how early and how cleanly the term appears: a title that *starts*
with it, then a *word* of the title starting with it, then anywhere in the title,
then the publisher. Shorter titles win ties, so "nature" offers *Nature* before
*Nature Reviews Methods Primers*. An ISSN is matched exactly instead.

### Settings · offline venue directories

One choice per directory, of two named answers — **Over the internet** or **On
this computer** — and no other control. There is no Remove and no Refresh: going
back to the internet leaves the file alone, and a copy older than a fortnight is
replaced on its own. The page itself is documented in
[`settings/README.md`](../settings/README.md), including why it is a pair of
named answers rather than a switch; what it changes is this:

| | Over the internet | On this computer |
|---|---|---|
| DOAJ | its API, ~0.1 s a search | the local copy, instant |
| Crossref | its API, ~0.55 s a search, from two letters up | the local copy, instant, from the first letter |

**Nothing is downloaded until "On this computer" is chosen**, and **going back to
the internet keeps the file** — so choosing the copy again is instant rather than
another download (measured: 2.5 s, no network). The preference and the presence
of the file are therefore two different facts, kept apart in
`venue_lookup.PREFS_FILE`: the internet answer means "search this online", not
"throw away the 18 MB you downloaded".

DOAJ used to be the exception — fetched automatically the first time anyone typed
into the whitelist box, which made a first run cost 25 MB it never mentioned, and
before that it had no online mode at all. Both are gone; see the note in
[`doaj.py`](doaj.py) about the 502 claim that justified it.

| | DOAJ | Crossref |
|---|---|---|
| Searched by | whitelist | blacklist |
| Journals | 23,118 | 167,307 |
| Download | ~25 MB CSV | ~51 MB CSV |
| Fetch + reduce | a few seconds | **18-21 s** |
| Kept on disk | 3.6 MB | 17.6 MB |
| One search | 2-34 ms | **14-23 ms** |
| Over the internet | its API: ~0.1 s a search, needs a connection | its API: ~0.55 s a search, needs a connection |

Two numbers, and both are true: the **download** is the directory's full CSV, the
**kept** figure is what is left after reducing it to the five fields a venue entry
uses. Both are on the Settings page now — the download beside the option that
incurs it, so it is known before it is chosen, and the kept figure in the line
that reports what is actually on disk.

**Is either enough to build a list with?** An entry matches on ISSN, domain or
name, so what matters is coverage of those — and every identifier an entry
carries is named on its row, so the scope of the run is readable off the list.
All of a venue's ISSNs share **one** pill (**ISSN 1980-4164 · 1413-2311**):
nearly every journal registers two, one for the print edition and one for the
online one, and directories hand back both. One pill each made a single journal
look like two, which is what it got asked; the tooltip on the pill says why there
are two numbers and that either one matches. Measured over the whole of both:

| | name | ISSN | publisher | website |
|---|---|---|---|---|
| DOAJ | 100% | 100% | 100% | 100% |
| Crossref | 100% | **82.1%** | 99.9% | **0%** |

So DOAJ can produce any of the three identifiers, and Crossref produces name and
ISSN but never a domain - Crossref records no websites at all. The status line on
the Settings page says exactly this, because it decides what a list built from
each can key on.

> **The two modes do not return the same set, and cannot.** The local copy is a
> substring scan over titles and publishers; the API is a text search that
> tokenises. Measured on DOAJ: `journal of` answers **4,521** locally and
> **13,489** online, and `power sources` answers **1** locally and **24** online —
> the local scan wants the phrase, the API matches the words. Neither is wrong,
> and the counts in the line under the box are honest about whichever answered.
> Crossref shows the same effect more mildly (`IEE`: 432 local, 415 online).

**Why the file and not the API.** `/journals` refuses `select` (a 400 - measured),
so it returns every field it holds, statistics included: 169 pages of ~3.7 MB is
about **630 MB and five and a half minutes** for the same data the title file
gives in one 50 MB request. That difference is the whole reason an offline
Crossref is practical.

With a local Crossref copy the box also drops its two-character floor and its
300 ms debounce down to DOAJ's 120 ms, because both existed to save a request and
there is no request. `directory_of()` decides that per render. The switch is
visible in the totals: `IEE` answers `8 of 432` from the local copy and `8 of 415`
from the live API - two genuinely different code paths over two snapshots of one
registry.

The parts that do not depend on *which* directory it is - the cache on disk, the
pre-lowered index, the ranking - live in [`venue_lookup.py`](venue_lookup.py), so
[`doaj.py`](doaj.py) and [`crossref.py`](crossref.py) hold only what differs:
where the file is and how to read a row of it.

> One measured speedup worth keeping: the ranking scan rejects a row with a
> single `in` test on the pre-lowered haystack before it runs any regex. Every
> rank implies the term is somewhere in that haystack, so it changes no result -
> and it took a Crossref search from **250 ms to 14 ms**.

#### Crossref: a live API, so a floor and a longer debounce

Out of the box [`crossref.py`](crossref.py) queries `/journals` live — the
downloaded copy above is the alternative, not the default, and this is what
applies until it is chosen. Crossref meters by **rate** rather than by budget,
and every response says so:

```
X-Rate-Limit-Limit: 5   X-Rate-Limit-Interval: 1s   X-Concurrency-Limit: 1
```

Five requests a second anonymously — doubled to ten, and three concurrent, for a
caller that identifies itself. That is far more headroom than OpenAlex's ~100
searches per 7 hours (what this box used to query), but a search still costs a
~0.55 s round trip and ~3 KB per journal returned, so this side:

- **does not search below two characters.** A single letter is a real query —
  `w` answers with 342 journals — but not an answer anyone can use, and it costs
  a round trip. The note says so rather than pretending nothing matched.
- **debounces at 300 ms** rather than DOAJ's 120, because a keystroke here goes
  over the network. Normal typing comes to one or two requests per word.
- **caches every term for an hour**, so extending and backspacing a word replays
  from cache.
- **treats a 404 on the ISSN path as an answer**, not a fault: no journal is
  registered under that ISSN.

Set `CROSSREF_MAILTO` in `.env` to join Crossref's polite pool; harmless if
unset. It is the **same variable** the run's own DOI cross-check reads
(`src/bealls_list_check/data_quality.py`) — one address, two unrelated uses of
one service, and no new dependency for this box.

**A Crossref journal record carries no website.** Its fields are the title, the
ISSNs, the publisher and usage statistics; there is no homepage. So a venue added
from the blacklist's directory is a name and its ISSNs, never a domain — which is
the safe direction anyway, and why the ⚠ shared-platform marker below is a DOAJ
matter. A publisher domain is still added deliberately, by typing it in.

#### The control

`st.selectbox` cannot do any of this: it filters options **client-side** over a
list it was given, so it can never look anything up while a word is being typed.
The box is therefore [`streamlit-searchbox`](https://pypi.org/project/streamlit-searchbox/),
which calls back into Python per keystroke.

Eight things about it are deliberate, and each was wrong before it was fixed:

| | Why |
|---|---|
| No chevron (`dropdown: {width: 0, height: 0}`) | A chevron promises a list to open. On an empty box there is none — this is a box you type into, not a menu that tolerates typing. |
| No "No options" message (`optionEmpty: "hidden"`) | It was being drawn under an untouched box, announcing the absence of results for a search nobody had run. |
| **Add** inside the box, at the right-hand end | It acts on the text in the box and nothing else, so it is the box's own button rather than a second control taking a quarter of the width from the thing being typed into. It has to be *overlaid* — the input is inside the component's iframe and no Streamlit widget can be rendered in there — so the container is the positioning context and the button is lifted out of flow onto the right edge, clearing the iframe's `z-index`. Out of flow it has no width to inherit, and Streamlit's label will shrink to one character per line (measured: 8 px wide, 67 px tall, inside a 28 px button), so every level from the button down is pinned to `nowrap` and `max-content`. |
| Add only once something is typed | On an empty box it offers to do something that cannot work. |
| Add becomes a **spinner** while a search is out | Typing into the blacklist costs a round trip to Crossref (~0.55 s, behind a 300 ms debounce), and for all of it the page still showed the frame from before the keystroke with Add looking as ready as ever. See below. |
| `style_absolute=True` | The menu lives *inside* the iframe, so the frame grows to ~350 px to show it. In normal flow that pushed the list and the JSON fold a third of a screen down on every keystroke and left the two columns at different heights; positioned absolutely it overlays instead. |
| A **box that looks like a box** (`searchbox_styles`) | The component leaves an unfocused control with `1px transparent`, which on this page is a field with no edge at all — placeholder text floating where a control should be. It is given the fill and border Streamlit gives its own inputs. Its `&:hover` has to be replaced too, or hovering puts the transparent border back. |
| The open menu is a **raised plane**, not a bordered region (`searchbox_styles`) | See below — this is the one that took three attempts. |
| An empty box **opens nothing** (`wrapper: {overflow: hidden}`) | See below. |

##### An empty box must not open an empty menu

The component decides for itself whether the menu is open:

```js
menuIsOpen: this.props.args.options && this.state.menu
```

`state.menu` is true from the moment the box has focus, so the whole guard is
`args.options` — an **empty JavaScript array, which is truthy**. Clicking an
untouched box therefore opened a menu holding nothing. Invisible for as long as
the menu was drawn in the page's own colour; a raised plane turned it into a
small empty panel appearing under an empty field.

Nothing can be done from the options side. Making them falsy does close the menu,
but `openMenu` maps over them *outside* that guard, so a falsy value throws —
measured, `Cannot read properties of null (reading 'map')` on the first click. No
JavaScript value is both falsy and mappable.

So the menu is left to open and is **clipped away instead**: `overflow: hidden`
on the component's own wrapper, which is an ancestor of the box the menu is
positioned against, and only for as long as there is nothing to show. A clipped
menu also adds no scrollable overflow, so the frame stays exactly the height of
the field (measured: 38 px with nothing to list, 366 px with eight matches).

That clip is why the focus ring is drawn `inset`. An outset ring would be clipped
along with the menu, and the field would lose its focus state exactly when it is
empty — which is when it is most likely to be focused.

##### Why the dropdown read as transparent

The component fills its menu with Streamlit's *page* background, so the open
dropdown was drawn in the exact colour of the page it was covering. It sizes
itself to its contents, so a two-line match covers the note and the top of the
venue list and stops — and the list box's border resumes immediately underneath,
its rows carrying on in the same colour, at the same width, in the same kind of
row. The two panels merge into one and the list's entries read as more matches.

A 1 px ring did not fix it, because the venue list box has a 1 px border too.
What separates an overlay from what it covers is **elevation**, and elevation is
not the same thing in the two themes:

- **Dark** — a shadow has almost nothing to darken against slate-900, so the lift
  is a *lighter surface*: menu and field on slate-800, one step above the page.
- **Light** — white on white needs the opposite: the surface stays white and the
  depth comes from a shadow, which does show on a light page.

Five mechanics decide how that is written, and none is a preference:

- **The ring is an `inset` box-shadow, not a `border`.** A border would change the
  list's box, and the list has to stay exactly the size of the container
  react-select draws behind it.
- **`borderRadius` is 4 px, matching react-select's own menu**, not the app's 8.
  The component forwards only the style keys it documents and `menu` is not one
  of them, so that container keeps react-select's white `neutral0`; the list sits
  in the same box and hides it. Round the list *more* than 4 px and the white
  reappears at all four corners — measured.
- **The list carries a bottom margin (`_MENU_GAP`), or there is no shadow at
  all.** Streamlit sizes the iframe to `document.body.scrollHeight`, which ended
  at the menu's bottom edge — so a drop shadow, which paints *outside* that edge,
  was clipped away entirely and the panel met the list box with nothing in
  between. The margin is counted rather than collapsed away, because a scrolling
  list is its own formatting context (measured: `scrollHeight` 118 → 142).
- **That margin grows the white container, so it is masked.** The extra band
  belongs to `menu`, which is white. It is covered by a third box-shadow: a copy
  of the list's own shape, in the page's colour, offset down by exactly
  `_MENU_GAP` so it lands on the band and nowhere else. A `spread` would have
  been simpler and was wrong — it reaches *upwards* too, and 26 px of it painted
  over the bottom of the field.
- **The menu's height is fixed (`_MENU_HEIGHT`), not sized to the match count.**
  This is the one that finally settled it. An opaque overlay sized to its
  contents ends wherever the number of matches puts it — and when that is
  *inside* a row of the list underneath, the part of the row below the edge stays
  on screen. Measured: a two-line DOAJ match ended the overlay at y=479 while the
  first row's ✕ ran from 462 to 493, leaving the bottom 14 px of a button sitting
  under the panel, looking like part of it. An edge that always lands in the same
  place cannot land inside a row, so the menu is given the exact height that puts
  it on the list box's bottom edge (measured: 294 px below the top of the search
  box, hit exactly). Content taller than that scrolls, which it did anyway past
  eight matches. The cost is a part-empty panel for one or two matches; the
  alternative is a sliced row, and a stable edge is worth more.

The height is summed from the bands between the two:

| | |
|---|---|
| 48 px | the two empty blocks `style_absolute` leaves to reserve the frame's space |
| 16 px | Streamlit's gap between elements |
| 40 px | the note band (`.bl-vl-note`) |
| `_VENUE_BOX_HEIGHT` | the list box itself |
| less `_MENU_OFFSET` | the 38 px field and the menu's own 8 px top margin |
| less `_MENU_GAP` | the gap below the panel |

**In pixels, and it has to be.** The height is applied *inside* the component's
iframe, whose document has its own root font size — Bootstrap's 16 px, which the
page's does not change. So `rem` there does not mean what `rem` means in the
bands it is matched against: written as `calc(6.5rem + …)` it stayed at 294 px
while a page at a 20 px root font grew its bands to 320 px, and the overlay fell
26 px short.

> **Known limit.** Two of the three bands are set in `rem` by Streamlit itself
> (the reserved band, and the gap between elements) and are not ours to pin —
> rules that set them are matched but lose to Streamlit's own, measured. So a
> reader whose browser default font size is not 16 px gets an overlay a little
> short of the list box (16 px at a 20 px root) or a little past it (12 px at
> 13 px). Short is the one that shows. At the default, which is what almost every
> reader has, it lands exactly.

##### The overlay has to win the stacking contest

`style_absolute` gives the iframe `z-index: 10`, and for a long time that looked
sufficient — Chromium hit-tests the iframe on top and nothing appears wrong. It
is not sufficient, because **a `z-index` only competes inside its own stacking
context**, and Streamlit wraps every element in a layout wrapper of its own.
Where that traps it, the note and the venue list's own text paint straight over
an open dropdown — reported and photographed, with
`8 of 31 DOAJ match(es) — choose one…` printed across the first match.

So the box **and** the wrapper Streamlit puts it in are both lifted to
`z-index: 30`. They are siblings of the note and the list within one block, so
lifting this subtree puts it above them whatever any wrapper does. Verified by
giving every layout wrapper and element container a competing `z-index` — the
overlay still wins with the lift, and reproduces the reported artifact exactly
without it.

The `:has()` rule degrades safely: a browser without it drops that selector and
keeps the container's own lift.

Naming an option's `backgroundColor` **flattens its hover**: the overrides are
spread over a value the component computes from the row's state, so the row under
the cursor stops changing colour. A raised menu has to name one, so the hover is
given back as a nested `:hover` — the styles reach react-select through emotion,
which honours it (verified in the generated rule:
`.css-…-option:hover { background-color: rgb(51,65,85) }`).

Two further details of the Add button are structural rather than cosmetic. Its
container is given the field's own height (`--bl-venuefield: 38px`) and centres
what is in it, instead of being offset by a hand-computed `top`: a pixel offset
is correct only while the button is exactly 28 px and the field exactly 38, and
goes visibly off-centre the moment a font size, a zoom level or a Streamlit
version changes either. And exactly **one** box in the chain is positioned —
absolutely positioning both the element container and `stButton` stacked their
offsets, putting the button 5 px below centre.

##### The spinner is CSS, and it has to be

The wait happens **inside one blocking call**: the component hands the term to
Python, Python queries, sets the options and reruns — and Streamlit paints only
when a run *finishes*, so there is no frame in between for Python to draw a
spinner into. A run that ends in a rerun has its output discarded anyway, which
is the same reason the "Added X" toast has to be queued (`notify_next_run`).

So it keys off Streamlit's own `data-test-script-state="running"` on `stApp` —
the only signal available. It cannot be narrowed to *which* box is busy: neither
`:focus-within` nor `iframe:focus` crosses into the component's frame (measured,
both false while the input inside it holds the caret). The scope that remains is
tighter than it sounds, because **Add only exists while a box holds a term nobody
has committed yet** — the same situation as "I am searching". A rerun with both
boxes empty has no button to spin.

While the search is out the button is not a button dressed as one: its **fill
goes with its label**, so what is left in the field is a spinner and nothing else.
Only the 47 × 28 px box it occupies stays, so the field's contents do not shuffle
when it comes back, and `pointer-events: none` matches the fact that it cannot be
pressed mid-run. The ring is drawn in the page's own border and accent rather than
in the accent's foreground, because it now sits on the field instead of on a blue
fill. Under `prefers-reduced-motion` it stops turning and the "not now" rests on
the missing label and fill.

##### The field paints its own outline, so it cannot blink out

Choosing a match remounts the component, and for the ~280 ms the new iframe takes
to boot there is **no field on screen at all** — the iframe exists but stands 0 px
tall (measured). Hiding Streamlit's loading skeleton stopped that gap from moving
anything, and by removing the grey placeholder it made the gap plainer to see: the
box vanished and came back.

So the band paints the field's own shape itself, as a `::before`. It sits under the
iframe — which is positioned, and so above it — and is invisible for as long as
there is an iframe to cover it; while there is not, it is what stays on screen.
Verified by catching the remount frame: with `ifrH: 0`, a 38 px field in
`rgb(30,41,59)` bordered `rgb(71,85,105)` is still there on dark, and
`rgb(241,245,249)` on `rgb(203,213,225)` on light — the same values the component
paints.

Those two colours come from Python (`field_placeholder_css`), because the
component's field is coloured from `st.context.theme` and CSS cannot ask which
theme is in force (see [above](#why-nothing-in-_tokens-branches-on-light-versus-dark)).
They are named once in `_FIELD_BG` / `_FIELD_EDGE` so the real field and the shape
standing in for it cannot drift apart.

#### Show more, rather than a silent top-eight

The menu opens with eight matches and its last row says how many it is holding
back — `▾ Show 20 more (130 further match(es))`. Choosing it widens the window by
twenty and re-runs the search; the term stays, and nothing is added.

That row exists because both searches report a **total**, not just the slice. A
menu showing eight of a hundred and thirty-eight, silently, claims to be the
answer when it is a sample. DOAJ's scan is uncapped so its total is exact;
Crossref reports `message.total-results`.

Two component defaults have to be turned off for it: `clear_on_submit` and
`edit_after_submit`. Show more *is* a selection, so both would fire — emptying
the box and closing the menu, throwing away the very term whose next page was
just requested. The clearing is done in the handler instead, which knows the
difference between turning a page and choosing a venue.

`search_function` only fires on a keystroke, so after a Show more the component
would still be holding its previous options. `_refresh_options` writes the longer
list straight into the component's `options_js` / `options_py` state, which it
re-reads on every render.

#### Why choosing a match has to rerun

`submit_function` is called **while the page is being drawn** — the box above it
has already been rendered by the time the handler runs. Everything the handler
writes therefore lands one render too late, and that one fact caused three
separate complaints:

| Symptom | Cause |
|---|---|
| "it doesn't add, then next time it does" | `_reset_box` writes no `options_py`, and the component falls back to handing on the **raw option index** when that key is missing. An integer has no `to_entry`, so the click after an add was dropped. |
| "randomly empties the input" | `_reset_box` changes the key that remounts the React side, which is the only thing that empties the box. With no rerun the box kept the term and emptied later, in the middle of whatever was typed next. |
| "the input shifts down and up" | a **loading skeleton**, see below. |

So the handler reruns as soon as it has acted. The component does exactly this
for its own `clear_on_submit`, from the same place. Confirmations are queued with
`notify_next_run` and emitted by `flush_notices` at the top of the next render,
because a toast raised in the run being abandoned goes with it.

#### Why the band is pinned, and the skeleton hidden

Choosing a match remounts the component — that is how the box clears — and while
the new iframe boots, Streamlit shows a **loading skeleton** in its element
container. The iframe is absolutely positioned and contributes no height, but the
skeleton is in flow. Sampled every frame across a pick: the band went 48 px →
92 px for ~280 ms → 48 px, carrying the note, the list box and the JSON fold 44 px
down the page and back. That is the input "going down and up".

Two rules fix it, and the first failed on its own in a way worth recording:

- **`min-height: 0`, not just `height`.** The container is a flex item, so its
  `min-height: auto` resolves to the automatic *minimum content size*, which
  outranks any height given to it. Measured: `height: 48px !important` computed to
  48 px while the box still stood 92 px tall. Zeroing the minimum is what lets the
  pin hold; the skeleton then overflows the band silently.
- **The skeleton is hidden inside this box.** Contained, it moves nothing — but it
  would still flash a 44 px grey placeholder across the note, and it tells nobody
  anything: the space is already reserved and the thing loading was on screen a
  moment ago.

A container whose whole job is to reserve a fixed band must not be sized by what
happens to be inside it (`_VENUE_BAND`).

The rerun exposed one more thing, latent until then. Streamlit **replays a
component's last value on every render**, so after a page turn the component
re-sends the option index the user clicked — and `_refresh_options` has just made
that index mean something else, the row that said *Show 20 more* now being the
ninth of twenty-nine venues. Measured: turning one page of a `journal of` search
put *Journal of Fungi* on the list unasked. A remount (`_remount_box`) is the
only lever, because only a new widget id retires the stored value; the term
survives it by being handed back as `default_searchterm`.

> A remounted react-select has no focus, so **after Show more the menu comes back
> closed** — the note reads `28 of 4521` and the longer list is one click on the
> box away. The alternative was to leave it open and suppress the replay by
> pretending the venue at that index had already been chosen, which makes that one
> venue silently unpickable until the user picks something else. An extra click
> beats a control that ignores you.

**Adding a venue contributes its title and ISSN, not its website.** A domain is
matched by host, so adding the site of a journal published on a platform
(`sciencedirect.com`, `nature.com`, a shared OJS install) would put every other
journal on that platform in scope; DOAJ matches whose host looks like a platform
are marked with a warning sign. A publisher domain is added deliberately, by
typing it into the same box.

Neither directory takes any part in a verdict. Being listed in DOAJ means a
journal passed its screening; being in Crossref means only that someone paid to
register its DOIs. The question this tool asks is whether the venue is on Beall's
List, which neither answers. The search only helps *name* a venue.

That distinction is why the whitelist's caption spells out that the papers it
leaves out are **not examined** rather than cleared. DOAJ is the canonical index of
*legitimate* journals, so putting a DOAJ search next to a box called "whitelist"
invites reading the box as *venues I trust, so don't flag them* — which is the
opposite of what it does.

The two lists are labelled by the familiar term, and **everything else is on the
?** beside it — what the list does, and what leaving it empty means. "Whitelist"
alone is ambiguous in this domain: it reads just as easily as *venues I consider
safe*, which is the opposite of what it does. So the explanation is not dropped,
only moved to where a question asked once is answered once, instead of being
restated across the column's widest lines on every look:

| Box | What its **?** says | CLI |
|---|---|---|
| **whitelist** | *Only check these venues.* Only papers from these venues are checked, and only they appear in the report — the rest are left out of it entirely, not listed as skipped. Leave empty to check every paper. | `--whitelist` |
| **blacklist** | *Also treat these venues as predatory.* They count as predatory for this run in addition to everything on Beall's List. Leave empty to use Beall's List as it stands. | `--blacklist` |

A paper the whitelist leaves out is **not examined**, which is not the same as
cleared; the workbook's Summary sheet counts them.

Both fold open to the same JSON the CLI flags take as a *file*, editable in
place:

```json
[{"name": "IEEE", "domain": "ieee.org"}, {"issns": ["2666-2485"]}, "Nature"]
```

The format is documented in
[`user_lists/README.md`](../../src/bealls_list_check/user_lists/README.md).

**All three checks show the venue lists.** Batch CSV showed none for a long
time, and the reason given was that it took neither — true of
[`batch_check.py`](../../src/bealls_list_check/batch_check/batch_check.py) as it
stood, and a gap in that script rather than anything about the check. It cost
more than a missing control: the app offers the CSV as an add-on to a run that
may have had both lists, so one run could hand back a workbook and a CSV that
disagreed about the same papers, with only a caption to warn anyone. The script
takes both flags now and the section is offered everywhere.

One asymmetry survives and is stated on Step 4's hover: under Batch CSV a
whitelist decides how many *rows* the file has, because a paper it excludes is
not checked and so is not a row. That is the property scripts reading the file
lean on, so it is said where the run is confirmed rather than left to be
discovered.

The step's pill names the options actually in force, each list with the number of
venues on it (`whitelist (3)`) rather than merely the fact that it is not empty.
It is read from `st.session_state` rather than from the widgets: the header is
drawn above the controls it summarises, and Streamlit has already written the
current rerun's values there. It counts the *list*, not the JSON box's text, so
the pill and the rows drawn below it cannot disagree.
