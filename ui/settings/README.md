# The Settings page

> Deep-dive companion to [`settings_page.py`](settings_page.py). The
> [web app's README](../README.md) gives the short version and links here.

The page holds one question, asked twice: **where is this venue directory
searched?** What the two directories are, what each covers and what makes them
opposites is in [the venues deep-dive](../venues/README.md#settings--offline-venue-directories);
this page is the choice and nothing else.

## Why it is two named answers and not a switch

It was a switch per directory, and the switch was wrong in a way no wording
rescued. A switch has an *off*, and an off is read as a feature withheld — so the
page said, in effect, *the whitelist cannot search until you switch this on*,
which is not true and never was. Both positions are working states. The box
searches either way; only the place it looks changes.

So the control names both:

| | Over the internet | On this computer |
|---|---|---|
| What it does | asks the directory's own search API | searches a downloaded copy of the whole journal list |
| Speed | DOAJ ~0.1 s, Crossref ~0.5 s a search | instant, both |
| Needs a connection | yes | no |
| Stored | nothing | DOAJ 3.6 MB, Crossref 17.6 MB |
| Download | none | DOAJ 25 MB / a few seconds, Crossref 51 MB / ~20 s |

Radio options rather than a segmented control or a switch, for the one property
that matters here: a radio cannot be deselected, so there is no third state to
handle, and every state the page can be in is one a reader can point at.

## The three bands, in the order the question is asked

Each directory renders the same three:

1. **What it is** — the name, which of Step 3's boxes searches it, and what it
   covers. The choice below is then about something the reader can picture.
2. **The choice** — both answers, each with its price in the caption underneath.
   This is the part the old page did not have: the cost of the option *not*
   selected was in a tooltip, so the only way to find out what switching would do
   was to switch. Now neither has to be tried.
3. **What is on disk** — the only band that changes by itself: how many journals,
   how big, and how long ago the copy arrived.

The captions are held to 21rem (`styles.py`) because the two options are a
comparison and have to sit side by side to read as one. Wider, and they wrap to
separate rows, which turns *this or that* back into a list.

## What the status line can say

| State | Line | Tone |
|---|---|---|
| Internet, no copy | *Searching DOAJ over the internet · nothing is stored on this computer* | muted |
| Internet, copy kept | *…· the 3.6 MB copy is still here, so going back costs no download* | muted |
| Copy, readable | *Searching a copy on this computer · 23,118 journals · 3.6 MB · downloaded 2 hours ago* | affirmative |
| Copy, damaged | *The copy of DOAJ on this computer could not be read — choose "Over the internet", then "On this computer" again to download it afresh* | amber |
| Copy chosen, none there | *No copy here yet — DOAJ is being searched over the internet until one arrives* | muted |

Searching over the internet is **not** amber and not a warning: it is one of the
two answers and the one the app starts on. Amber is kept for the single case that
needs a hand — a file that is present and cannot be read.

That last case is also why the page tests whether the copy is *usable* rather
than merely *present* before deciding whether to download. `cache_status` reports
a damaged file as a journal count of `-1`; treating that as "already have it"
made the recovery line advise a round trip that did nothing, since going to the
internet and back would have found the same broken file and congratulated itself
on it.

This is the one place that distinction is worth its cost. `cache_status` reports
the journal count, and counting the records means parsing the file — 405 ms over
Crossref's 17.6 MB. Affordable for a page that renders when somebody opens it;
the venue boxes ask the cheaper `venue_lookup.cache_present()` instead, and
[`venues/README.md`](../venues/README.md#what-a-keystroke-actually-costs) records
what it cost when they did not.

## What there is no control for

- **No Remove.** Choosing *Over the internet* leaves the file where it is, which
  is what makes choosing the copy again instant (measured: 2.5 s, no network).
  The preference and the presence of the file are two separate facts, kept apart
  in `venue_lookup.PREFS_FILE`.
- **No Refresh.** A copy older than a fortnight still answers every search, so
  nothing is withheld while it ages; choosing *On this computer* is what replaces
  it, on the same two clicks that recover a damaged one. It used to be replaced
  automatically — by a venue box, because `load_journals` treats a stale cache as
  no cache, which made the fortnight rule a 51 MB download from inside a
  keystroke and contradicted the promise two lines below.
- **Nothing is downloaded until it is asked for here.** That is the point of the
  page existing at all: a first run of this app should cost nothing it was not
  asked for.
