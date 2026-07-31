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
from config import CORPUS_DIR, CORPUS_GLOB, SNAPSHOT_PATH, STATUS_FILL_COLORS
from match import BeallIndex, match_paper
from user_lists import load_user_entries, WhitelistMatcher
import data_quality                        # shared Crossref client (for the reachability probe)
from common import input_quality          # the one shared input data-quality validator
from resolve import resolve_review        # DOI-based resolution of 'review' verdicts
from report import save_workbook          # Excel workbook writer; re-exported here so
#                                           bealls_llm_check.py can keep calling
#                                           bealls_list_check.save_workbook(...) unchanged.


# =============================================================================
# LOADING
# =============================================================================

def load_snapshot():
    """Load the vendored Beall's List snapshot, or exit with guidance."""
    if not SNAPSHOT_PATH.exists():
        sys.exit(
            f"Snapshot not found: {SNAPSHOT_PATH}\n"
            "Run scrape_bealls_list/scrape_bealls_list.py first to vendor the Beall's List."
        )
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_index(blacklist_path=None):
    """Load the snapshot (plus an optional user blacklist) into a BeallIndex.

    The blacklist entries are tagged ``list_source="blacklist"`` and matched
    exactly like real Beall entries (asserting ``on_list``).  Returns
    ``(index, snapshot, n_blacklist_added)``.
    """
    snapshot = load_snapshot()
    entries = list(snapshot["entries"])
    n_black = 0
    if blacklist_path:
        added = load_user_entries(blacklist_path, "blacklist")
        entries += added
        n_black = len(added)
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

    Returns ``(results, sorted_corpus_files, n_excluded)``.
    """
    results, corpus_files, n_excluded = [], set(), 0
    for source_file, paper in load_corpus():
        corpus_files.add(source_file)
        if whitelist is not None and whitelist.active and not whitelist.in_scope(paper):
            n_excluded += 1
            continue                     # outside the whitelist: not in this run at all
        # match_paper never raises — a bad record returns an 'error' result.
        r = match_paper(index, paper, source_file)
        use_cr = crossref == "all" or (crossref == "flagged"
                                       and r["status"] in ("on_list", "review"))
        r["data_quality"] = input_quality.validate_input(paper, use_crossref=use_cr)
        # The DOI resolvers all hit the network (Crossref), so they only run when
        # Crossref is enabled.  With --crossref off (or no internet — see
        # resolve_crossref_mode) the whole pass is purely offline and cannot stall
        # on a slow connection.
        if use_cr:
            resolve_review(r, paper, index)
        results.append(r)
    return results, sorted(corpus_files), n_excluded


def load_corpus():
    """Yield (source_file, paper) for every record across all corpus files.

    Deduplicates by ``paperId`` across files (the corpus files are disjoint in
    practice, but this guards against accidental overlap).
    """
    files = sorted(CORPUS_DIR.glob(CORPUS_GLOB))
    if not files:
        sys.exit(f"No corpus files matching {CORPUS_GLOB} in {CORPUS_DIR}")
    seen = set()
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            records = json.load(f)
        kept = 0
        for paper in records:
            pid = paper.get("paperId") or paper.get("title")
            if pid in seen:
                continue
            seen.add(pid)
            kept += 1
            yield fp.name, paper
        print(f"  loaded {kept:>6} papers from {fp.name}")


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
    parser.add_argument("--crossref", choices=["off", "flagged", "all"], default="all",
                        help="resolve/cross-check DOIs against Crossref: all (the default; "
                             "every DOI), flagged (on_list/review only), or off (fully offline). "
                             "Used automatically when online and falls back to offline with no "
                             "internet, so you never need to pass this unless you want 'off'.")
    args = parser.parse_args(argv)

    print("=" * 70)
    print("Beall's List predatory-venue check")
    print("=" * 70)

    index, snapshot, n_black = build_index(args.blacklist)
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
