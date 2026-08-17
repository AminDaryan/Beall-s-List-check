"""Fetch the Kscien Organization's non-recommended-venue lists into a snapshot.

Kscien (kscien.org, Kurdistan region of Iraq) publishes five lists of venues it
does not recommend.  This module turns them into the same ``{name, url, domain,
normalized_name, issns, list_source}`` entries the Beall's snapshot uses, so the
matcher treats both catalogues identically.

Why a snapshot rather than live queries per paper: the same three reasons the
Beall's scraper gives — a run is reproducible, it works offline, and the file
records *when* it was taken, which matters for a catalogue that is contested.

**The snapshot is NOT vendored in this repository.**  Kscien's site carries
"Copyright © 2026 Kscien – All Rights Reserved" and publishes no licence or terms
page, so redistributing a copy is not ours to do.  The file is written into
``data/`` which git ignores; each user fetches their own.  That is also why
``config.PROVIDERS["kscien"]["vendored"]`` is False: the check must say so rather
than assume the data is present.

Access route
------------
Kscien runs WordPress, which exposes its content over the standard REST API.  It
is not a service Kscien advertises or documents, so treat it as an
implementation detail that can change — every request here degrades to a clear
error rather than a partial snapshot.

  GET /wp-json/wp/v2/publishing-taxonomy?per_page=100   the five lists + counts
  GET /wp-json/wp/v2/predatory-publishing?per_page=100  the entries, paginated

``per_page`` is capped at 100 by WordPress itself (101 returns
``rest_invalid_param``), so a full pull of ~3,539 entries is ~36 requests.

Keeping it current cheaply
--------------------------
A full pull is only needed once.  Afterwards:

  * ``?per_page=1&orderby=modified&order=desc`` reports the newest change in ONE
    request, so a run can ask "is there anything new?" for the price of one call;
  * ``?modified_after=<iso>`` returns only the entries changed since the
    snapshot was taken — in practice a handful, since Kscien has modified nine
    entries in the last twenty months despite describing the list as daily;
  * ``X-WP-Total`` gives the live entry count, which is how DELETIONS are
    noticed: ``modified_after`` cannot report a removed entry, so when the live
    total is lower than the snapshot's, this module falls back to a full pull.

Usage
-----
    python src/bealls_list_check/fetch_kscien/fetch_kscien.py            # sync
    python src/bealls_list_check/fetch_kscien/fetch_kscien.py --check    # report only
    python src/bealls_list_check/fetch_kscien/fetch_kscien.py --full     # force full pull
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
for _p in [_ROOT, *(d for d in _ROOT.iterdir() if d.is_dir() and (d / f"{d.name}.py").exists())]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from normalize import normalize_name, normalize_host, extract_issns

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://kscien.org/wp-json/wp/v2"
POSTS = f"{BASE}/predatory-publishing"
TAXONOMY = f"{BASE}/publishing-taxonomy"
USER_AGENT = "Mozilla/5.0 (compatible; bealls-list-check/1.0)"
PER_PAGE = 100                      # WordPress's own maximum
TIMEOUT = 30
SNAPSHOT_PATH = _ROOT / "data" / "kscien_snapshot.json"

# Kscien's five lists mapped onto the categories the matcher already knows.
#
# Mapped by the taxonomy's SLUG, fetched at run time, because term ids are
# database keys that can change.  The ids observed on 2026-08-17 are recorded
# only as a fallback for a run whose taxonomy request fails.
#
# The mapping is the load-bearing part of adding a catalogue: an unmapped list
# would reach the matcher with a category no rule governs, which
# ``build_index`` refuses at load (see config.KNOWN_LIST_SOURCES).
# Slugs verified against the live taxonomy on 2026-08-17.  Note that two of them
# do NOT follow their display names — the list shown as "Non-Recommended
# Publishers" has the slug "publishers", and "Non-Recommended Conferences" has
# "predatory-conferences" — which is exactly why the unmapped-list warning below
# is loud rather than silent: guessing these from the display names dropped half
# the catalogue on the first run, and nothing in the output would have said so.
SLUG_TO_CATEGORY = {
    "publishers": "publishers",
    "standalone-journals": "standalone_journal",
    "hijacked-journals": "hijacked",
    "misleading-metrics": "misleading_metric",
    "predatory-conferences": "predatory_conference",
}
FALLBACK_TERM_IDS = {
    10: "hijacked", 11: "misleading_metric", 12: "predatory_conference",
    13: "publishers", 14: "standalone_journal",
}

_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def clean_title(raw):
    """A displayable venue name from Kscien's ``title.rendered``.

    Their titles carry artefacts that would otherwise reach the report and the
    match keys: U+FFFD replacement characters from a mis-decoded import, U+00A0
    non-breaking spaces, HTML entities, and occasional letter-spacing.

    Cleaning matters for more than looks.  The entry's ``normalized_name`` is
    derived from this cleaned string, so cleaning here is what keeps
    ``normalize_name(entry["name"]) == entry["normalized_name"]`` — the property
    a later stage relies on when it has only the display name to work from.
    """
    text = unescape(_TAG_RE.sub(" ", str(raw or "")))
    text = text.replace("�", " ").replace("\xa0", " ")
    # Drop other format/control characters (zero-width joiners, BOMs, ...).
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc"))
    return re.sub(r"\s+", " ", text).strip()


def first_url(content_html):
    """The venue's own website from Kscien's ``content.rendered``, or "".

    The field holds one or more bare URLs wrapped in list markup and nothing
    else; ~11 of the entries have it empty, which is why the caller must accept
    an entry with no website (it can still match by name).
    """
    match = _URL_RE.search(unescape(str(content_html or "")))
    return match.group(0).rstrip(").,;") if match else ""


def make_entry(item, term_to_category):
    """One Kscien record -> a snapshot entry, or None if unusable.

    Returns None when the record carries neither a usable name nor a website —
    there would be nothing to match on — or when its list is one this build does
    not map.  Both cases are counted and reported by the caller rather than
    passed silently to the matcher.
    """
    name = clean_title((item.get("title") or {}).get("rendered"))
    url = first_url((item.get("content") or {}).get("rendered"))
    domain = normalize_host(url) if url else ""
    normalized = normalize_name(name)
    if not (normalized or domain):
        return None

    terms = item.get("publishing-taxonomy") or []
    category = next((term_to_category[t] for t in terms if t in term_to_category), None)
    if category is None:
        # Kscien assigns a handful of entries to no list at all — 7 of 3,539, and
        # six of those are among its most recent additions, so dropping them
        # would discard precisely the newest material. They are kept under a
        # category of their own, which is weak: Kscien has published the venue
        # but not said which list it belongs to, so a match is surfaced for a
        # human and never asserted. Guessing the list from the name (several
        # read like publishers) would be inventing a claim the source did not
        # make.
        category = "unclassified"

    return {
        "name": name or domain,
        "url": url,
        "domain": domain,
        "normalized_name": normalized,
        # Kscien publishes no ISSNs at all (0 of 3,539 as of 2026-08-17), so this
        # is always empty; kept for schema parity with the Beall entries.
        "issns": extract_issns(name),
        "list_source": category,
        # Their own record ids and timestamps, so an incremental sync can tell
        # an edited entry from a new one.
        "kscien_id": item.get("id"),
        "modified": item.get("modified", ""),
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def _get(session, url, params, what):
    """One GET, or exit with the reason.  A partial snapshot is worse than none."""
    try:
        resp = session.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        sys.exit(f"Could not reach Kscien while fetching {what}: {exc}")
    if resp.status_code != 200:
        sys.exit(f"Kscien returned HTTP {resp.status_code} for {what} "
                 f"({resp.url}). Nothing was written.")
    try:
        return resp, resp.json()
    except ValueError:
        sys.exit(f"Kscien returned a non-JSON body for {what} ({resp.url}). "
                 "The undocumented REST route may have changed.")


def fetch_term_map(session):
    """``{term_id: category}`` for the five lists, by slug."""
    _resp, terms = _get(session, TAXONOMY, {"per_page": PER_PAGE}, "the list taxonomy")
    mapping, unmapped = {}, []
    for t in terms:
        slug = (t.get("slug") or "").strip().lower()
        category = SLUG_TO_CATEGORY.get(slug)
        if category:
            mapping[t["id"]] = category
        else:
            unmapped.append(f"{t.get('name')!r} (slug {slug!r}, id {t.get('id')})")
    if unmapped:
        # Loud, not silent: a new Kscien list would otherwise be dropped without
        # anyone noticing that the snapshot had stopped covering everything.
        print("  ! Kscien publishes lists this build does not map, so they are "
              "NOT in the snapshot:")
        for u in unmapped:
            print(f"      {u}")
        print("    Add them to SLUG_TO_CATEGORY (and to config.KNOWN_LIST_SOURCES) "
              "to include them.")
    if not mapping:
        print("  ! No list could be mapped from the taxonomy; falling back to the "
              "term ids recorded on 2026-08-17.")
        mapping = dict(FALLBACK_TERM_IDS)
    return mapping


def live_state(session):
    """``(total, newest_modified)`` — the cheap freshness probe, one request."""
    resp, items = _get(session, POSTS,
                       {"per_page": 1, "orderby": "modified", "order": "desc",
                        "_fields": "id,modified"},
                       "the newest change")
    total = int(resp.headers.get("X-WP-Total") or 0)
    newest = (items[0].get("modified") or "") if items else ""
    return total, newest


def fetch_entries(session, term_map, modified_after=None):
    """Every entry, or only those changed since *modified_after*.

    Returns ``(entries, skipped)``.  Pages until the reported page count is
    exhausted rather than until a short page arrives, so a truncated response
    cannot be mistaken for the end of the list.
    """
    params = {"per_page": PER_PAGE, "orderby": "id", "order": "asc",
              "_fields": "id,modified,title,content,publishing-taxonomy"}
    if modified_after:
        params["modified_after"] = modified_after

    entries, skipped, page, pages = [], 0, 1, None
    while True:
        resp, items = _get(session, POSTS, {**params, "page": page},
                           f"entries (page {page})")
        if pages is None:
            pages = int(resp.headers.get("X-WP-TotalPages") or 1)
            total = resp.headers.get("X-WP-Total") or "?"
            scope = "changed" if modified_after else "total"
            print(f"  {total} entries {scope}, {pages} page(s) of {PER_PAGE}")
        for item in items:
            entry = make_entry(item, term_map)
            if entry is None:
                skipped += 1
            else:
                entries.append(entry)
        if page >= pages or not items:
            break
        page += 1
        time.sleep(0.2)                   # courteous to an endpoint nobody documented
    return entries, skipped


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def load_local():
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _count_duplicates(entries):
    """How many entries share an index key with an earlier one.

    Reported rather than removed.  The snapshot is a record of what Kscien
    publishes, and its counts have to reconcile with Kscien's own — a reader
    checking "the tool says 498 conferences, the site says 498" must get
    agreement.  Collapsing duplicates here broke that: it silently removed 59
    entries, 44 of them conferences.

    Removing them also buys nothing.  ``BeallIndex`` stores a LIST of entries per
    domain/name/ISSN key and ``_best`` chooses among them, so a repeated venue
    costs a slightly larger index and changes no verdict.  (The Beall's scraper
    does deduplicate, but it parses HTML, where the same anchor can genuinely
    appear twice as page furniture.  This API returns records with distinct ids;
    there is no artefact to clean up.)
    """
    seen, dupes = set(), 0
    for e in entries:
        key = (e["list_source"], e["normalized_name"], e["domain"])
        if key in seen:
            dupes += 1
        seen.add(key)
    return dupes


def write_snapshot(entries, total, newest, note_extra=""):
    duplicates = _count_duplicates(entries)
    counts = {}
    for e in entries:
        counts[e["list_source"]] = counts.get(e["list_source"], 0) + 1
    snapshot = {
        "meta": {
            "source": "https://kscien.org/non-recommended-journals-lists/",
            "api": POSTS,
            "scraped_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total_entries": len(entries),
            "counts": counts,
            # Reported, not removed — see _count_duplicates.  Kept in the
            # metadata so the snapshot's own quirks are on the record.
            "internal_duplicates": duplicates,
            # The values the next run's incremental sync compares against.
            "live_total": total,
            "newest_modified": newest,
            # Which categories THIS build knew how to map.  A snapshot taken by a
            # build that mapped fewer lists is incomplete in a way the freshness
            # probe cannot see — nothing upstream changed — so the mapping is
            # recorded and a change to it forces a full pull.  The first run of
            # this module mapped three of the five lists and looked healthy.
            "mapped_categories": sorted(set(SLUG_TO_CATEGORY.values())),
            "note": (
                "Kscien Organization's non-recommended lists, fetched from the "
                "site's WordPress REST API. Kscien publishes no per-entry "
                "evidence, so a match records which list an entry is on and how "
                "it matched, never why Kscien listed it. Kscien states 'All "
                "Rights Reserved' and publishes no licence, so this file is not "
                "redistributed with the tool. " + note_extra
            ).strip(),
        },
        "entries": entries,
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    return snapshot


def sync(force_full=False, check_only=False):
    """Bring the local snapshot up to date as cheaply as correctness allows."""
    session = _session()
    local = load_local()
    total, newest = live_state(session)

    have = (local or {}).get("meta", {})
    have_total = int(have.get("live_total") or have.get("total_entries") or 0)
    have_newest = have.get("newest_modified") or ""

    want_categories = sorted(set(SLUG_TO_CATEGORY.values()))
    have_categories = have.get("mapped_categories")

    if local is None:
        reason = "no local copy yet"
        mode = "full"
    elif force_full:
        reason = "full pull requested"
        mode = "full"
    elif have_categories != want_categories:
        # Absent as well as different: a snapshot that does not record what it
        # covered cannot be trusted to cover everything this build maps.
        # Nothing upstream changed, so the freshness probe would have said "up to
        # date" while the snapshot silently omitted whole lists.
        missing = sorted(set(want_categories) - set(have_categories or ()))
        reason = ("this build maps lists the local copy does not cover"
                  + (f" ({', '.join(missing)})" if missing else ""))
        mode = "full"
    elif total < have_total:
        # modified_after cannot report a removal, so a shrinking total is the
        # only signal that one happened.
        reason = f"entries were removed upstream ({have_total} -> {total})"
        mode = "full"
    elif newest and newest > have_newest:
        reason = f"changes since {have_newest or 'the last sync'}"
        mode = "incremental"
    elif total != have_total:
        reason = f"entry count changed ({have_total} -> {total})"
        mode = "full"
    else:
        print(f"Kscien: up to date ({total} entries, newest change {newest or '?'}).")
        return local

    print(f"Kscien: {reason} -> {mode} sync.")
    if check_only:
        print("  (--check: nothing fetched)")
        return local

    if mode == "incremental":
        term_map = fetch_term_map(session)
        changed, skipped = fetch_entries(session, term_map, modified_after=have_newest)
        kept = {(e["kscien_id"]) for e in changed if e.get("kscien_id")}
        merged = [e for e in local["entries"] if e.get("kscien_id") not in kept]
        merged += changed
        snapshot = write_snapshot(merged, total, newest,
                                 f"Last update was incremental: {len(changed)} "
                                 f"entr(ies) changed.")
        print(f"  {len(changed)} changed, {skipped} unusable, "
              f"{snapshot['meta']['total_entries']} in the snapshot.")
        return snapshot

    term_map = fetch_term_map(session)
    entries, skipped = fetch_entries(session, term_map)
    snapshot = write_snapshot(entries, total, newest)
    print(f"  {len(entries)} usable, {skipped} skipped (no name/website, or a list "
          f"this build does not map).")
    for src, n in sorted(snapshot["meta"]["counts"].items(), key=lambda kv: -kv[1]):
        print(f"    {src:24} {n}")
    print(f"Saved -> {SNAPSHOT_PATH}")
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch the Kscien Organization's non-recommended-venue lists "
                    "into a local snapshot for the check to match against.")
    parser.add_argument("--full", action="store_true",
                        help="re-fetch every entry instead of only what changed")
    parser.add_argument("--check", action="store_true",
                        help="report whether an update is needed, fetch nothing")
    args = parser.parse_args(argv)
    print("=" * 70)
    print("Kscien non-recommended lists — fetch")
    print("=" * 70)
    sync(force_full=args.full, check_only=args.check)


if __name__ == "__main__":
    main()
