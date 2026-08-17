"""Batch Beall's List check — machine-readable input and output.

A thin command-line wrapper around the same engine as the main check
(bealls_list_check.py), intended for embedding in a server or a script:

    input   one file of Semantic Scholar paper records — .jsonl/.ndjson
            (one JSON object per line) or .json (an array of objects)
    output  one CSV with exactly three columns: SS-ID, DOI, Status —
            one row per input record, in input order (no deduplication)

The input record structure and the possible Status values are documented in
README.md next to this script. Usage:

    python src/bealls_list_check/batch_check/batch_check.py papers.jsonl -o results.csv

Crossref DOI resolution is on by default (uncertain 'review' verdicts are
settled via the paper's DOI) and degrades to fully offline automatically when
Crossref is unreachable — same behaviour as the main check. Pass
``--crossref off`` to force offline.

The CSV carries only the verdict. For the evidence behind each verdict (what
matched and why, data-quality warnings), run the main check instead — it writes
a fully audited Excel workbook.
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
# Package root + each foldered module (foo/foo.py) on the path, so the flat
# 'from x import y' import style works (same shim as the other entry scripts).
for _p in [_ROOT, *(d for d in _ROOT.iterdir() if d.is_dir() and (d / f"{d.name}.py").exists())]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(_ROOT.parent))          # src/ -> common

import bealls_list_check as det                # build_index + resolve_crossref_mode
import config                                  # the reference-list registry
from match import match_paper
from progress import Progress, crossref_note   # the live "[i/N] ..." ticker
from resolve import resolve_review
from user_lists import WhitelistMatcher, load_user_entries

CSV_COLUMNS = ("SS-ID", "DOI", "Status")


# =============================================================================
# INPUT
# =============================================================================

def load_records(path: Path) -> list:
    """Return the paper records in *path*, routed by suffix.

    ``.jsonl`` / ``.ndjson`` are read line-by-line (one JSON object per line,
    blank lines ignored); any other suffix is read as one JSON document (an
    array of objects, or a single object). Malformed input exits nonzero with
    a message naming the offending line — nothing is half-processed, so a
    caller can treat any nonzero exit as "the input file is bad".
    """
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return _load_jsonl(path)
    return _load_json(path)


def _load_jsonl(path: Path) -> list:
    records = []
    # utf-8-sig: tolerate the BOM that Windows tools often prepend.
    with open(path, encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.exit(f"{path.name}, line {lineno}: not valid JSON ({exc})")
            if not isinstance(rec, dict):
                sys.exit(f"{path.name}, line {lineno}: expected a JSON object "
                         f"(one paper record), got {type(rec).__name__}")
            records.append(rec)
    if not records:
        sys.exit(f"{path.name}: no records found (every line is blank)")
    return records


def _load_json(path: Path) -> list:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        sys.exit(f"{path.name}: not valid JSON ({exc}) — for line-delimited "
                 f"input use a .jsonl/.ndjson suffix")
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list) and parsed and all(isinstance(x, dict) for x in parsed):
        return parsed
    sys.exit(f"{path.name}: expected a JSON array of paper objects "
             f"(or a single object)")


# =============================================================================
# MAIN
# =============================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Batch Beall's List check: read Semantic Scholar paper records "
                    "from one file, write a CSV with one row per record — "
                    "SS-ID, DOI, Status.",
        epilog="The input record structure is documented in the README.md "
               "next to this script.")
    parser.add_argument("input",
                        help=".jsonl/.ndjson file (one Semantic Scholar paper record "
                             "per line) or .json file (an array of records)")
    parser.add_argument("-o", "--output",
                        help="CSV path to write (default: the input path with "
                             "a .csv suffix)")
    parser.add_argument("--crossref", choices=["on", "off"], default="on",
                        help="settle uncertain 'review' verdicts via the paper's DOI "
                             "in Crossref (default: on; falls back to off automatically "
                             "when Crossref is unreachable)")
    # The same two lists the main check takes.  This script went without them for
    # a while, which was not a decision so much as a gap: the web app offers this
    # CSV as an add-on to a run that may have had both, and a CSV that quietly
    # ignored them disagreed with the workbook beside it about the same papers.
    parser.add_argument("--whitelist", help="JSON list of venues; only papers whose "
                                            "venue is in it are checked and written")
    parser.add_argument("--blacklist", help="JSON list of venues to add to Beall's List "
                                            "as predatory before matching")
    # Same flag as the main check, for the same reason --whitelist/--blacklist are
    # here: the web app offers this CSV alongside a workbook from the same run, and
    # a CSV screened against different lists would disagree with the workbook beside
    # it about the same papers.
    parser.add_argument("--list", action="append", dest="providers",
                        choices=sorted(config.PROVIDERS),
                        help="a reference list to check against; repeat for several "
                             f"(default: {', '.join(config.DEFAULT_PROVIDERS)})")
    args = parser.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.is_file():
        sys.exit(f"Input file not found: {in_path}")
    out_path = Path(args.output) if args.output else in_path.with_suffix(".csv")

    records = load_records(in_path)
    index, snapshot, n_black = det.build_index(args.blacklist, providers=args.providers)
    print(f"Loaded {len(records)} record(s) from {in_path.name}; snapshot has "
          f"{len(snapshot['entries'])} entries "
          f"(scraped {snapshot['meta'].get('scraped_utc', '?')}).")
    if n_black:
        print(f"Added {n_black} user blacklist entr(ies) as predatory.")
    whitelist = None
    if args.whitelist:
        whitelist = WhitelistMatcher(load_user_entries(args.whitelist, "whitelist"))
        print("Whitelist active — only papers from those venues are checked, so this "
              "CSV has one row per *checked* record rather than per supplied record.")
    crossref = det.resolve_crossref_mode(args.crossref)

    started = time.time()
    counts = {}
    # Same ticker the workbook checks use: with Crossref on, this loop spends its
    # time on network round-trips and would otherwise print nothing at all
    # between the "Loaded N record(s)" line above and the summary below.
    progress = Progress("papers", note=crossref_note(crossref))
    progress.add_total(len(records))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        n_excluded = 0
        for paper in records:
            # Selected out by the whitelist: not checked, and so not a row.  The
            # count is printed below, because a CSV that is shorter than the file
            # it came from has to say why somewhere.
            if whitelist is not None and whitelist.active and not whitelist.in_scope(paper):
                n_excluded += 1
                progress.tick()          # read, though it earns no row
                continue
            # match_paper never raises — a bad record yields an 'error' row.
            r = match_paper(index, paper, in_path.name)
            if crossref != "off":
                resolve_review(r, paper, index)
            writer.writerow([r["paperId"], r["doi"], r["status"]])
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            progress.tick()
    progress.close()

    print(f"Checked {sum(counts.values())} paper(s) in {time.time() - started:.1f}s")
    if n_excluded:
        print(f"  ({n_excluded} more were outside the whitelist and are not in the CSV)")
    for status in ("on_list", "review", "clean", "no_venue", "error"):
        if status in counts:
            print(f"  {status:9}: {counts[status]}")
    print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
