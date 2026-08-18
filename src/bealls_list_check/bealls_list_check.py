"""Beall's List predatory-venue check — the conductor (main entry point).

Reads top-to-bottom as the whole pipeline: load the snapshot → match every
corpus paper → (optionally) resolve 'review' verdicts via the DOI → write the
Excel workbook. The heavy lifting lives in one well-named module per concern, so
this file stays short:

    bealls_list_check.py  (this file)  the conductor + CLI — the pipeline.
    match.py                           decide each paper's verdict from S2 data.
    resolve.py                         upgrade/clear 'review' verdicts via the DOI.
    data_quality.py                    input sanity checks + the Crossref client.
    descriptions.py                    all the plain-English text ("what we say").
    report.py                          render the Excel workbook ("how we draw it").
    progress.py                        the live "[i/N] ..." ticker both long
                                       passes report through.

The workbook has five sheets:

  * Results      — one multi-row block per paper, colored by status, with full
                   evidence (what matched, why) so any flag can be audited.
  * Flagged only — just the on_list + review rows, so you are not scrolling
                   tens of thousands of clean/preprint rows to find the hits.
                   (The optional LLM pass adds an 'LLM review' column here.)
  * Data quality — only the papers whose Semantic Scholar record looks suspect
                   (missing/merged venue, malformed DOI/ISSN, …), each shown
                   with the specific issue — so a verdict isn't trusted blindly.
  * Summary      — counts per status + per Beall list, and the run metadata
                   (snapshot date, corpus files) needed to interpret them.
  * Legend       — a plain-language data dictionary for every column and value.

Run the scraper first (scrape_bealls_list/scrape_bealls_list.py) to produce the
snapshot, then:
  python bealls_list_check.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Package root + each foldered module (foo/foo.py) on the path, so the flat
# 'from x import y' imports keep working after match/resolve/user_lists moved
# into their own folders.
for _p in [_HERE, *(d for d in _HERE.iterdir() if d.is_dir() and (d / f"{d.name}.py").exists())]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(_HERE.parent))          # src/ -> common
from config import (CORPUS_DIR, CORPUS_GLOB, SNAPSHOT_PATH, STATUS_FILL_COLORS,
                    PROVIDERS, DEFAULT_PROVIDERS, KNOWN_LIST_SOURCES)
from match import BeallIndex, match_paper
from user_lists import load_user_entries, WhitelistMatcher
import data_quality                        # shared Crossref client (for the reachability probe)
from common import input_quality          # the one shared input data-quality validator
from resolve import resolve_review        # DOI-based resolution of 'review' verdicts
from progress import Progress, crossref_note   # the live "[i/N] ..." ticker
from report import save_workbook          # Excel workbook writer; re-exported here so
#                                           bealls_llm_check.py can keep calling
#                                           bealls_list_check.save_workbook(...) unchanged.


# How many papers are matched offline before their DOIs are fetched together.
# Only bounds how much of the corpus is held at once and how long one fetch runs
# — the concurrency itself is Crossref's (3 for the polite pool), and a few dozen
# DOIs already saturate that, so this trades nothing away.  Small enough that a
# batch's fetch is tens of seconds rather than minutes, since a batch is the unit
# the progress line's counter moves in.
CROSSREF_BATCH = 250


# =============================================================================
# LOADING
# =============================================================================

def load_snapshot(provider="beall"):
    """Load one provider's vendored snapshot, or exit with guidance.

    Defaults to Beall's List, so every existing caller keeps its behaviour.
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        sys.exit(f"Unknown reference list: {provider!r}. "
                 f"Known: {', '.join(sorted(PROVIDERS))}.")
    path = spec["snapshot"]
    if not path.exists():
        # A list the repository does not ship has to say how to get it: silently
        # checking against nothing would report every paper as clean.
        fetch_with = spec.get("fetch_with")
        if provider == "beall":
            hint = ("Run scrape_bealls_list/scrape_bealls_list.py first to vendor "
                    "the Beall's List.")
        elif fetch_with:
            hint = (f"{spec['label']} is not shipped with this tool, so it has to be "
                    f"fetched once before it can be used:\n"
                    f"    python src/bealls_list_check/{fetch_with}\n"
                    f"Source: {spec.get('home', '')}")
        else:
            hint = f"No local copy of {spec['label']} yet."
        sys.exit(f"Snapshot not found: {path}\n{hint}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _validate_entries(entries, provider, label):
    """Fail the run if a provider hands over entries the matcher cannot treat.

    Checked at load, once, rather than per paper.  An unmapped ``list_source``
    is the dangerous case: it would miss ``WEAK_LIST_SOURCES`` and so be
    ASSERTED as a confident hit instead of downgraded to review, and it would
    miss ``FUZZY_LIST_SOURCES`` and silently lose the fuzzy tier.  Neither
    failure is visible in the output, which is exactly why it is caught here.
    """
    unknown = sorted({e.get("list_source") or "(missing)" for e in entries
                      if (e.get("list_source") or "") not in KNOWN_LIST_SOURCES})
    if unknown:
        sys.exit(
            f"{label} supplied entries whose category the check does not know: "
            f"{', '.join(unknown)}.\n"
            f"Known categories: {', '.join(sorted(KNOWN_LIST_SOURCES))}.\n"
            "A provider must map its own sub-lists onto these, because the "
            "weak-list downgrade and the fuzzy tier are keyed on them."
        )


def _merged_meta(loaded):
    """One metadata dict describing every provider a run used.

    Deliberately still a dict answering the same four keys the Summary sheet
    reads (``scraped_utc``, ``total_entries``, ``source``, ``note``), because
    ``save_workbook`` takes it positionally and three call sites read those keys.
    Turning it into a list would break all of them.

    ``scraped_utc`` reports the OLDEST date across the providers used: a report
    is only as current as the stalest list behind it.
    """
    metas = [m for _pid, _label, m in loaded]
    dates = sorted(m.get("scraped_utc", "") for m in metas if m.get("scraped_utc"))
    return {
        "scraped_utc": dates[0] if dates else "",
        "total_entries": sum(int(m.get("total_entries") or 0) for m in metas),
        "source": "; ".join(m.get("source", "") for m in metas if m.get("source")),
        "note": " ".join(m.get("note", "") for m in metas if m.get("note")),
        # The per-provider record, for the Summary sheet and for reproducibility.
        "providers": [
            {"provider": pid, "label": label,
             "scraped_utc": m.get("scraped_utc", ""),
             "total_entries": m.get("total_entries", ""),
             "source": m.get("source", "")}
            for pid, label, m in loaded
        ],
    }


def build_index(blacklist_path=None, *, providers=None):
    """Load the enabled providers (plus an optional user blacklist) into a BeallIndex.

    *providers* is an iterable of provider ids from ``config.PROVIDERS``; it
    defaults to ``DEFAULT_PROVIDERS`` (Beall's List alone), so a caller that does
    not pass it behaves exactly as before.  Keyword-only, and the return shape is
    unchanged — ``(index, snapshot, n_blacklist_added)`` — because three call
    sites unpack that tuple and read ``snapshot["entries"]`` / ``["meta"]``.

    Every entry is stamped with the provider it came from.  The user's blacklist
    is concatenated AFTER the provider entries, never filtered with them: a
    filter that could drop the user's own list would silently disable
    ``BeallIndex.user_index`` and with it the whole blacklist tier, while the run
    still printed that the entries had been added.
    """
    provider_ids = tuple(providers) if providers else tuple(DEFAULT_PROVIDERS)
    entries, loaded = [], []
    for pid in provider_ids:
        snap = load_snapshot(pid)
        label = PROVIDERS[pid]["label"]
        own = [{**e, "provider": pid} for e in snap["entries"]]
        _validate_entries(own, pid, label)
        entries += own
        loaded.append((pid, label, snap.get("meta") or {}))

    n_black = 0
    if blacklist_path:
        added = load_user_entries(blacklist_path, "blacklist")
        entries += added                      # after the filter, never inside it
        n_black = len(added)

    snapshot = {"entries": entries, "meta": _merged_meta(loaded)}
    return BeallIndex(entries), snapshot, n_black


def classify_corpus(index, whitelist=None, crossref="off"):
    """Classify every corpus paper, applying the optional whitelist scope filter.

    When a *whitelist* is active it **selects the corpus**: a paper whose venue
    is not in it is dropped before anything else happens, so it is not matched,
    not validated, not counted and not written to any sheet.  The report is then
    a report about the whitelisted venues alone — 500 papers in and 50 of them
    whitelisted means 50 rows, and every count on the Summary sheet is out of 50.

    Papers carrying no venue at all are dropped too: an empty whitelist means
    "check everything", but a non-empty one is a positive list, and a record with
    nothing to match cannot be on it.

    That the rest were excluded is not silently lost — the count comes back from
    here, is printed by the caller and has its own row on the Summary sheet.  It
    is a number, though, not a set of rows: a paper outside the whitelist was
    never examined, and a report is a poor place to keep something it has nothing
    to say about.

    Each result also gets a ``data_quality`` list (see data_quality.py): offline
    structural/consistency checks always, plus a Crossref cross-check when
    *crossref* is ``"all"`` (every DOI) or ``"flagged"`` (only on_list/review
    rows — far cheaper on a big corpus).

    The DOI-based review resolvers (hijacked clearance, alternate-URL,
    open-access-PDF and name_exact confirm/clear — see resolve.py) all use
    Crossref, so they run only when *crossref* is enabled.  With ``"off"`` the
    whole pass is purely offline and never touches the network.  (The command-line
    entry points default to ``"all"`` and downgrade to ``"off"`` when Crossref is
    unreachable — see :func:`resolve_crossref_mode`; this parameter itself defaults
    to ``"off"`` so a direct caller opts in explicitly.)

    Ticks a :class:`Progress` line as it goes (see that class for why), so a run
    whose Crossref pass takes tens of minutes reports where it is rather than
    going silent between "Loading corpus ..." and the summary.

    Returns ``(results, sorted_corpus_files, n_excluded)``.
    """
    results, corpus_files, n_excluded = [], set(), 0
    progress = Progress("papers", note=crossref_note(crossref))
    for batch in _batched(load_corpus(progress), CROSSREF_BATCH):
        # Pass 1 — offline. Matching is pure computation (~5,500 papers/second),
        # so every verdict in the batch is known before a single request is made,
        # which is what lets the next step know WHICH DOIs this batch will need.
        staged = []
        for source_file, paper in batch:
            corpus_files.add(source_file)
            if whitelist is not None and whitelist.active and not whitelist.in_scope(paper):
                n_excluded += 1
                progress.tick()          # counted: it was read, even though it is not reported on
                continue                 # outside the whitelist: not in this run at all
            # match_paper never raises — a bad record returns an 'error' result.
            r = match_paper(index, paper, source_file)
            use_cr = crossref == "all" or (crossref == "flagged"
                                           and r["status"] in ("on_list", "review"))
            staged.append((paper, r, use_cr))

        # Pass 2 — network, concurrently. Every lookup the batch is about to make
        # is for a paper's OWN DOI, so warming those fills the cache for all of
        # them at once. Without this the run proceeds at one round-trip per paper
        # (~8/second however fast the connection), because each lookup happens in
        # the middle of classifying its own paper and nothing overlaps.
        if crossref != "off":
            data_quality.prefetch(
                [((p.get("externalIds") or {}).get("DOI") or "").strip()
                 for p, _r, use_cr in staged if use_cr],
                # Nothing finishes during a prefetch, so the paper counter cannot
                # move; the heartbeat keeps the rate, the estimate and the lookup
                # tally updating so the wait still reads as work.
                on_progress=progress.heartbeat)

        # Pass 3 — finish each paper. Every Crossref call below is a cache hit.
        for paper, r, use_cr in staged:
            r["data_quality"] = input_quality.validate_input(paper, use_crossref=use_cr)
            # The DOI resolvers all hit the network (Crossref), so they only run
            # when Crossref is enabled.  With --crossref off (or no internet — see
            # resolve_crossref_mode) the whole pass is purely offline and cannot
            # stall on a slow connection.
            if use_cr:
                resolve_review(r, paper, index)
            results.append(r)
            progress.tick()
    progress.close()
    return results, sorted(corpus_files), n_excluded


def _batched(iterable, size):
    """Yield lists of up to *size* items — the corpus in bites, not all at once.

    The corpus is streamed rather than held in memory, so batching is what lets a
    group of DOIs be fetched together without loading every paper first.
    """
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_corpus(progress=None):
    """Yield (source_file, paper) for every record across all corpus files.

    Deduplicates by ``paperId`` across files (the corpus files are disjoint in
    practice, but this guards against accidental overlap).

    Each file's size is announced as soon as it is parsed and, when a *progress*
    ticker is given, added to its denominator.  Both have to happen here rather
    than in the caller because this is a generator: it is consumed lazily, one
    paper at a time, so anything printed after its loops only reaches the reader
    once the whole corpus has been classified — which on a big run is tens of
    minutes too late to be progress at all.  (This message used to be printed
    there, and reported the post-dedupe count; the raw record count is what can
    honestly be known up front, and duplicates are reported separately below.)
    """
    files = sorted(CORPUS_DIR.glob(CORPUS_GLOB))
    if not files:
        sys.exit(f"No corpus files matching {CORPUS_GLOB} in {CORPUS_DIR}")
    seen, duplicates = set(), 0
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            records = json.load(f)
        print(f"  read {len(records):>6} record(s) from {fp.name}", flush=True)
        if progress is not None:
            progress.add_total(len(records))
        for paper in records:
            pid = paper.get("paperId") or paper.get("title")
            if pid in seen:
                duplicates += 1
                if progress is not None:
                    progress.add_total(-1)      # never yielded: keep the denominator reachable
                continue
            seen.add(pid)
            yield fp.name, paper
    if duplicates:
        print(f"  ({duplicates} duplicate record(s) skipped)", flush=True)


# =============================================================================
# MAIN
# =============================================================================

def resolve_crossref_mode(requested):
    """Resolve the requested ``--crossref`` mode to the effective one, degrading
    to ``"off"`` when Crossref can't be reached.

    Crossref is used by DEFAULT (``requested`` defaults to ``"all"``): the check
    resolves and cross-checks DOIs whenever there's internet, and silently falls
    back to a fully-offline run when there isn't — so a user never has to ask for
    it, and a run never stalls or fails just because the machine is offline. Pass
    ``--crossref off`` to force offline even when online. Prints a one-line status
    and returns the effective mode.
    """
    if requested == "off":
        print("Crossref: off (fully offline, by request).")
        return "off"
    if data_quality.crossref_reachable():
        print(f"Crossref: {requested} (online - resolving DOIs against the registry).")
        return requested
    print("Crossref: requested but unreachable (no internet?) - continuing fully offline.")
    return "off"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Beall's List predatory-venue check.")
    parser.add_argument("--whitelist", help="JSON list of venues; the report covers only "
                                            "papers whose venue is in it — the rest are left "
                                            "out of the run entirely")
    parser.add_argument("--blacklist", help="JSON list of venues to add to Beall's List "
                                            "as predatory before matching")
    # Repeatable rather than comma-separated, so argparse validates each value
    # against `choices` and `-h` enumerates what is legal — the same idiom
    # --crossref already uses. A comma list would need a custom parser and its
    # own error message, and `nargs="+"` would swallow batch_check.py's
    # positional input argument.
    parser.add_argument("--list", action="append", dest="providers",
                        choices=sorted(PROVIDERS),
                        help="a reference list to check against; repeat for several "
                             f"(default: {', '.join(DEFAULT_PROVIDERS)}). A list that is "
                             "not shipped with the tool must be fetched first — the run "
                             "says how.")
    parser.add_argument("--crossref", choices=["off", "flagged", "all"], default="all",
                        help="resolve/cross-check DOIs against Crossref: all (the default; "
                             "every DOI), flagged (on_list/review only), or off (fully offline). "
                             "Used automatically when online and falls back to offline with no "
                             "internet, so you never need to pass this unless you want 'off'.")
    args = parser.parse_args(argv)

    print("=" * 70)
    print("Beall's List predatory-venue check")
    print("=" * 70)

    index, snapshot, n_black = build_index(args.blacklist, providers=args.providers)
    # Name every list in force, with its own size and date, because "3045 entries"
    # says nothing about which catalogues produced them once there can be more
    # than one — and a report has to be able to state what it screened against.
    for p in snapshot["meta"].get("providers", []):
        print(f"  {p['label']}: {p['total_entries']} entries "
              f"(as of {(p['scraped_utc'] or '?')[:10]})")
    print(f"Loaded snapshot: {len(snapshot['entries'])} entries "
          f"(scraped {snapshot['meta'].get('scraped_utc', '?')})")
    if n_black:
        print(f"Added {n_black} user blacklist entr(ies) as predatory.")

    whitelist = None
    if args.whitelist:
        whitelist = WhitelistMatcher(load_user_entries(args.whitelist, "whitelist"))
        print(f"Whitelist active ({len(whitelist.issns)} ISSN(s) + "
              f"{len(whitelist.domains)} domain(s) + "
              f"{len(whitelist.name_phrases)} name(s)) — the report will cover "
              "these venues only; every other paper is left out of it.")

    crossref_mode = resolve_crossref_mode(args.crossref)

    print(f"Loading corpus from {CORPUS_DIR} ...")
    started = time.time()
    results, corpus_files, n_excluded = classify_corpus(index, whitelist, crossref=crossref_mode)

    elapsed = time.time() - started
    out_path, n_flagged = save_workbook(results, snapshot["meta"], corpus_files,
                                        n_excluded=n_excluded)

    counts = {s: 0 for s in STATUS_FILL_COLORS}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("-" * 70)
    print(f"Checked {len(results)} papers in {elapsed:.1f}s")
    if n_excluded:
        # Said here as well as on the Summary sheet: a run that reports on 50 of
        # the 500 papers handed to it must say so where the 50 is printed, or the
        # number reads as the size of the corpus.
        print(f"  ({n_excluded} more were outside the whitelist and are not in the report)")
    if whitelist is not None and whitelist.active and not results:
        print("  Nothing matched the whitelist — the report is empty. Check the names, "
              "websites and ISSNs in it against the papers' venues.")
    for status in ("on_list", "review", "clean", "no_venue", "error"):
        print(f"  {status:13}: {counts.get(status, 0)}")
    print(f"Flagged (on_list + review): {n_flagged}")
    print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
