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
from match import match_paper
from resolve import resolve_review

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
    args = parser.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.is_file():
        sys.exit(f"Input file not found: {in_path}")
    out_path = Path(args.output) if args.output else in_path.with_suffix(".csv")

    records = load_records(in_path)
    index, snapshot, _ = det.build_index()
    print(f"Loaded {len(records)} record(s) from {in_path.name}; snapshot has "
          f"{len(snapshot['entries'])} entries "
          f"(scraped {snapshot['meta'].get('scraped_utc', '?')}).")
    crossref = det.resolve_crossref_mode(args.crossref)

    started = time.time()
    counts = {}
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for paper in records:
            # match_paper never raises — a bad record yields an 'error' row.
            r = match_paper(index, paper, in_path.name)
            if crossref != "off":
                resolve_review(r, paper, index)
            writer.writerow([r["paperId"], r["doi"], r["status"]])
            counts[r["status"]] = counts.get(r["status"], 0) + 1

    print(f"Checked {len(records)} paper(s) in {time.time() - started:.1f}s")
    for status in ("on_list", "review", "clean", "no_venue", "error"):
        if status in counts:
            print(f"  {status:9}: {counts[status]}")
    print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
