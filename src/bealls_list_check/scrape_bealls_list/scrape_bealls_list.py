"""Scrape beallslist.net and vendor a local snapshot of every list.

Beall's List is a list of *publishers* and *journals*, not papers.  To decide
whether a paper sits on the list we first need a clean, structured copy of the
list itself.  This script fetches every list page on the (unofficial, archived)
beallslist.net site, parses each entry into ``{name, url, domain, ...}`` and
writes them all to ``data/bealls_snapshot.json``.

Why vendor a snapshot instead of scraping live on every run?
  * Reproducibility — a run today and a run next month compare against the
    same list, so result differences come from the papers, not the website.
  * Resilience — the matcher works offline and does not depend on the site
    being up (it has gone down before).
  * Honesty — the snapshot records *when* it was taken, which matters because
    Beall's List is frozen/contested and not authoritative ground truth.

Pages scraped (all five "list" pages; the informational pages are skipped):
  * /                      predatory *publishers*        (name + URL list)
  * /standalone-journals   standalone predatory journals (name + URL list)
  * /hijacked-journals     hijacked journals             (HTML table)
  * /misleading-metrics    fake-metric companies         (name + URL list)
  * /vanity-press          vanity presses                (name + URL list)

Note on scope: a paper venue realistically only ever matches the publishers,
standalone-journals or hijacked lists.  misleading-metrics (metric vendors) and
vanity-press (book publishers) are included because the user asked for
"everything on the site", but each entry is tagged with ``list_source`` so a
match against those low-relevance lists is visible and filterable.

For the hijacked table only the *hijacker/predatory* column is taken — the
"authentic journal" column lists legitimate journals, and flagging those would
be a false positive.

Usage:
  python scrape_bealls_list/scrape_bealls_list.py
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import requests

# This module now lives in <pkg>/scrape_bealls_list/; put the package root and
# every foldered module (foo/foo.py) on the path so the flat 'from x import y'
# imports still work (same shim as match/resolve).
_ROOT = Path(__file__).resolve().parent.parent
for _p in [_ROOT, *(d for d in _ROOT.iterdir() if d.is_dir() and (d / f"{d.name}.py").exists())]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from normalize import normalize_name, normalize_host, extract_issns

# Make name printing safe on Windows consoles (cp1252 can't encode some names).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://beallslist.net"
USER_AGENT = "Mozilla/5.0 (compatible; bealls-list-check/1.0)"
# The snapshot lives in the package's data/ folder (one level up now that this
# script sits in its own scrape_bealls_list/ folder).
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "bealls_snapshot.json"

# (path, list_source, parse_mode)
LIST_PAGES = [
    ("/",                    "publishers",         "links"),
    ("/standalone-journals", "standalone_journal", "links"),
    ("/hijacked-journals",   "hijacked",           "hijacked_table"),
    ("/misleading-metrics",  "misleading_metric",  "links"),
    ("/vanity-press",        "vanity_press",       "links"),
]

# Anchor texts that are page chrome, not list entries.
_CHROME_TEXTS = {
    "", "home", "contact", "about", "skip to content", "read more",
    "comments", "reply", "log in", "search", "menu",
}

_TAG_RE = re.compile(r"<[^>]+>")
_A_TAG_RE = re.compile(r'<a\b[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                       re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"href=\"(https?://[^\"]+)\"", re.IGNORECASE)
_HEADING_RE = re.compile(r"<(h[1-4])[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_UL_RE = re.compile(r"<ul[^>]*>(.*?)</ul>", re.IGNORECASE | re.DOTALL)
_HTTP_HREF_RE = re.compile(r'href="https?://[^"]+"', re.IGNORECASE)


def _text(html_fragment: str) -> str:
    """Strip tags, decode HTML entities, and collapse whitespace.

    Decoding matters for matching: a listed name like "Science &amp; Education"
    must become "Science & Education" so it normalizes the same way as the venue
    string Semantic Scholar reports (which carries a real "&"); otherwise the
    stray "amp" defeats the name match.
    """
    text = unescape(_TAG_RE.sub("", html_fragment))
    return re.sub(r"\s+", " ", text).strip()


def _has_external_link(fragment: str) -> bool:
    """True if *fragment* contains an http(s) link to a non-beallslist host."""
    return any("beallslist.net" not in m.group(0).lower()
               for m in _HTTP_HREF_RE.finditer(fragment))


def strip_excluded_sections(html: str) -> str:
    """Remove any ``<hN>Excluded …</hN>`` section (heading + content up to the
    next heading) from a page before its entries are parsed.

    beallslist.net's Publishers page ends with a section titled
    "Excluded – decide after reading" that lists publishers the author
    deliberately did NOT put on the list (e.g. MDPI: "I decided not to include
    MDPI on the list itself"). Those are explicitly *not* listed, so scraping
    them as entries is wrong. Only this section is removed; the real
    "Original list" and "Update" sections above it are untouched.
    """
    headings = list(_HEADING_RE.finditer(html))
    cuts = []
    for i, m in enumerate(headings):
        if "exclud" in _text(m.group(2)).lower():
            end = headings[i + 1].start() if i + 1 < len(headings) else len(html)
            cuts.append((m.start(), end))
    for start, end in reversed(cuts):      # back-to-front keeps earlier indices valid
        html = html[:start] + html[end:]
    return html


def fetch(path: str, retries: int = 4, backoff: float = 3.0) -> str:
    """GET a page and return its decoded HTML, retrying transient failures.

    beallslist.net intermittently returns 503 (Service Unavailable) under load,
    and a single blip should not lose a whole scrape (which would silently
    produce an incomplete snapshot). Retry a few times with linear backoff and
    only raise if every attempt fails.
    """
    url = BASE + path
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as err:
            last_err = err
            if attempt < retries:
                wait = backoff * attempt
                print(f"\n  (fetch {path} failed: {err}; retry {attempt}/{retries - 1} "
                      f"in {wait:.0f}s)", end=" ", flush=True)
                time.sleep(wait)
    raise last_err


def _entry_anchors(li_html: str) -> list:
    """External ``(url, name)`` links inside one ``<li>``, in order.

    The name is the anchor's own text, EXCEPT where the character immediately
    before the link is a letter.  beallslist.net sometimes opens the link one
    character into the name — ``<li>I<a ...>nternational Journal ...</a>`` — and
    the anchor text alone would file that venue under a name missing its first
    letter, which then matches nothing.

    Only a lone capital joined to a lower-case continuation counts, because that
    is the shape of a split word and nothing else is. Gluing whatever letters
    happened to precede the link turned "The<a>Journal of Reading and Literacy"
    into "heJournal of Reading and Literacy".
    """
    out = []
    for m in _A_TAG_RE.finditer(li_html):
        url = m.group(1)
        if "beallslist.net" in url.lower():
            continue
        name = _text(m.group(2))
        glued = re.search(r"(?<![A-Za-z])([A-Z])$", li_html[:m.start()])
        if glued and name[:1].islower():
            name = glued.group(1) + name
        out.append((url, name, _text(li_html[:m.start()])))
    return out


def _clean_alias(alias: str) -> str:
    """Tidy the name taken from the text before a "see" cross-reference.

    Splitting a bullet at its "see" cuts through whatever punctuation happened
    to be open, so "Lambert Academic Publishing (LAP, see ..." arrives as
    "Lambert Academic Publishing (LAP" — a name carrying a bracket it never
    closes, which normalizes to something no venue will ever match. Close the
    cut by dropping the unfinished bracket, then any trailing aside that only
    points elsewhere.
    """
    alias = alias.strip(" ,;:.-—")
    while alias.count("(") > alias.count(")"):
        alias = alias[:alias.rindex("(")].strip(" ,;:.-—")
    alias = re.sub(r"\s*\((?:also|note|see)\b[^)]*\)\s*$", "", alias,
                   flags=re.IGNORECASE).strip(" ,;:.-—")
    return alias


def parse_links(html: str, list_source: str) -> list:
    """Parse a ``<li><a href=...>Name</a>`` list page into entries.

    Three kinds of entry are captured:

      * linked    — the bullet's first external link.  The link does NOT have to
                    open the bullet: this used to require ``<li>`` to be followed
                    immediately by ``<a>``, so every bullet written "The <a>Grant
                    Medical Journals</a> (GMJ)" was dropped whole — 31 publishers
                    and journals absent from the snapshot, each one a venue that
                    would have been reported clean.
      * alias     — the name before a "see" cross-reference ("Canadian Academy of
                    Oriental and Occidental Culture see CSCanada"), and any
                    FURTHER link in the same bullet, which is how a publisher's
                    second brand is listed ("Kowsar Publishing (also Brieflands)").
                    Both are the same predatory outfit under another name, so a
                    paper filed under either has to match.
      * name-only — ``<li>Name</li>`` with no link at all.  Taken ONLY from
                    ``<ul>`` blocks that already hold real linked entries, so
                    navigation and footer lists never leak in.

    A bullet that reads as a sentence is not an entry.  The criteria on the
    misleading-metrics page are full sentences carrying an inline link, and
    reading that link as a venue would invent one.
    """
    entries = []
    linked_names = set()

    # 1) Linked entries, and the aliases that share a bullet with them.
    for m in _LI_RE.finditer(html):
        inner = m.group(1)
        whole = _text(inner)
        anchors = _entry_anchors(inner)
        if not anchors:
            continue
        url, name, before = anchors[0]
        # An entry's bullet opens with its name; a prose bullet reaches its link
        # only part-way through a sentence. So the test is how much text stands
        # BEFORE the link, not whether the bullet ends in a full stop — the
        # criteria on the misleading-metrics page are sentences carrying an
        # inline link, while "Spring City Culture International Group Pte Ltd.
        # Also: here." is an entry that happens to end in one.
        if len(before) > 25 and not re.search(r"\bsee\b[:,]?\s*$", before, re.IGNORECASE):
            continue
        if name.lower() in _CHROME_TEXTS or len(name) < 2:
            continue
        linked_names.add(name.lower())
        entries.append(_make_entry(name, url, list_source))

        # "Alias see Canonical" — the alias is the text before the "see".
        if re.search(r"\bsee\b", whole, re.IGNORECASE):
            alias = _clean_alias(re.split(r"\bsee\b", whole, maxsplit=1,
                                          flags=re.IGNORECASE)[0])
            if (len(alias) >= 8 and len(alias.split()) >= 2
                    and alias.lower() not in _CHROME_TEXTS
                    and alias.lower() not in linked_names):
                linked_names.add(alias.lower())
                entries.append(_make_entry(alias, "", list_source))

        # A bullet's SECOND link is deliberately ignored. It is sometimes the
        # publisher's other brand (Kowsar Publishing "(also Brieflands)"), but it
        # is far more often a pointer — "(see here)", a bare URL, "member of
        # COPE", "transferred to SAGE" — and no filter told those apart
        # reliably: reading them as entries put publicationethics.org and
        # uk.sagepub.com on the predatory list. Missing one rebrand costs a
        # recall gap; flagging SAGE costs the report its credibility, and a
        # false flag is the error this check must not make.

    # 2) Name-only entries, scoped to <ul> blocks that are real entry lists.
    seen = set()
    for ul in _UL_RE.findall(html):
        if not _has_external_link(ul):
            continue                                  # nav / footer / info list
        for m in _LI_RE.finditer(ul):
            inner = m.group(1)
            if "beallslist.net" in inner.lower() or _HTTP_HREF_RE.search(inner):
                continue                              # linked (handled above) or nav
            name = _text(inner)
            if " SEE " in name:                       # "Alias SEE Canonical" -> keep the alias
                name = name.split(" SEE ", 1)[0].strip()
            key = name.lower()
            if (key in _CHROME_TEXTS or key in linked_names or key in seen
                    or len(name) < 8 or len(name.split()) < 2):
                continue
            seen.add(key)
            entries.append(_make_entry(name, "", list_source))

    return entries


def parse_hijacked_table(html: str, list_source: str) -> list:
    """Parse the hijacked-journals table.

    Row layout is ``[Hijacker/Predatory Journal, Authentic Journal]``.  The first
    cell (the fake journal) plus its URL becomes the listed entry — only the
    counterfeit is ever flagged.  The second cell (the genuine journal the
    counterfeit impersonates) is also captured, as ``authentic_name`` /
    ``authentic_url`` on the entry, so a paper actually published at the authentic
    journal can later be cleared instead of left as a doubtful name match.
    """
    entries = []
    for row in _TR_RE.findall(html):
        cells = _TD_RE.findall(row)
        if not cells:
            continue
        hijacker_html = cells[0]
        name = _text(hijacker_html)
        if not name or name.lower() in {"hijacker/predatory journal",
                                        "hijacked/predatory journal",
                                        "predatory journal"}:
            continue
        href_match = _HREF_RE.search(hijacker_html)
        url = href_match.group(1) if href_match else ""
        # Authentic journal = the second column (the real journal being impersonated).
        authentic_name = authentic_url = ""
        if len(cells) > 1:
            authentic_html = cells[1]
            authentic_name = _text(authentic_html)
            am = _HREF_RE.search(authentic_html)
            authentic_url = am.group(1) if am else ""
        # ISSNs sometimes appear in the row text; capture from the whole row.
        issns = extract_issns(_text(row))
        entries.append(_make_entry(name, url, list_source, issns=issns,
                                   authentic_name=authentic_name,
                                   authentic_url=authentic_url))
    return entries


def _make_entry(name: str, url: str, list_source: str, issns=None,
                authentic_name: str = "", authentic_url: str = "") -> dict:
    """Build a normalized snapshot entry.

    Hijacked entries also carry the authentic (impersonated) journal's name, URL
    and domain when the source table provides them, so a paper actually published
    at the authentic journal can be distinguished from the predatory clone.
    """
    entry = {
        "name": name,
        "url": url,
        "domain": normalize_host(url),
        "normalized_name": normalize_name(name),
        "issns": issns or [],
        "list_source": list_source,
    }
    if authentic_name or authentic_url:
        entry["authentic_name"] = authentic_name
        entry["authentic_url"] = authentic_url
        entry["authentic_domain"] = normalize_host(authentic_url)
    return entry


def scrape() -> dict:
    """Scrape all list pages and return the snapshot dict."""
    all_entries = []
    counts = {}
    for step, (path, list_source, mode) in enumerate(LIST_PAGES, 1):
        # "[i/N]" is this project's progress token — the UI reads it to fill a
        # ring, the same way it reads the corpus pass and the LLM pass.
        print(f"[{step}/{len(LIST_PAGES)}] Fetching {BASE + path} ...",
              end=" ", flush=True)
        html = strip_excluded_sections(fetch(path))
        if mode == "hijacked_table":
            entries = parse_hijacked_table(html, list_source)
        else:
            entries = parse_links(html, list_source)
        print(f"{len(entries)} entries")
        counts[list_source] = len(entries)
        all_entries.extend(entries)
        time.sleep(0.5)

    # Deduplicate within the combined list by (list_source, normalized_name, domain).
    seen, deduped = set(), []
    for e in all_entries:
        key = (e["list_source"], e["normalized_name"], e["domain"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    return {
        "meta": {
            "source": BASE,
            "scraped_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pages": [p for p, _, _ in LIST_PAGES],
            "counts_raw": counts,
            "total_entries": len(deduped),
            "note": (
                "Unofficial archived copy of Beall's List. Appearance on this "
                "list is an allegation as of the scrape date, not authoritative "
                "proof a venue is predatory. The list is frozen/contested."
            ),
        },
        "entries": deduped,
    }


def main():
    snapshot = scrape()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    meta = snapshot["meta"]
    print("-" * 60)
    print(f"Scraped {meta['total_entries']} unique entries:")
    for src, n in meta["counts_raw"].items():
        print(f"  {src:20} {n}")
    with_domain = sum(1 for e in snapshot["entries"] if e["domain"])
    print(f"Entries with a usable domain: {with_domain}")
    print(f"Saved snapshot -> {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
