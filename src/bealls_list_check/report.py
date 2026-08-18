"""Render the results into the combined Excel workbook — the "how we draw it" layer.

This module is pure presentation: it turns a list of result dicts (produced by
the matcher + resolvers) into a styled openpyxl workbook with five sheets —
Results, Flagged only, Data quality, Summary, Legend.  It contains NO matching
or classification logic; the wording it prints comes from ``descriptions.py``.

The one public entry point is :func:`save_workbook`.  Everything else is a
private helper:

  * ``_*_subrows(r)``       — turn one result into the labelled rows of a column.
  * ``_write_*_sheet(...)`` — render each sheet.
  * layout helpers (``_row_spans``, ``_fast_merge``, ``_estimate_row_height``) —
    the per-paper "block" geometry (see the COLUMNS comment below).
"""

import sys
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import RESULTS_DIR, RESULTS_FILENAME, STATUS_FILL_COLORS
from normalize import split_list_sources
from progress import Progress
from descriptions import (
    _on_list_why, _list_plain, _listed_on, _signal_plain, _NONMATCH_NOTES, _nonmatch_note,
    _LEGEND_SECTIONS, _LEGEND_TITLE,
)


# Per-paper layout: each paper occupies a BLOCK whose height is the larger of
# ROWS_PER_PAPER and the number of 'Mentioned in' venues.  Within a block every
# column's logical cells (built by the _*_subrows helpers below) are spread
# evenly and MERGED to fill the block, so there are no blank padding cells: a
# column holding a single value becomes one cell spanning the whole block (e.g. a
# lone linked venue), while 'Mentioned in' grows a taller block to fit every
# linked venue and the other columns keep their 4 / 4 / 3 fields merged to fit.
# The DOI row (-> article), each 'Mentioned in' venue (-> the venue's own site)
# and the 'Listed as' row (-> Beall entry) are hyperlinks.  Paper / Semantic
# Scholar venue / Mentioned in all describe the same paper, so they share a
# merged "Paper specifications" header band (see _write_results_header).  Data
# quality is no longer a column — it gets its own sheet.
COLUMNS = [
    ("#",                      6),
    ("Status",                 12),
    ("Paper",                  60),
    ("Semantic Scholar venue", 44),
    ("Mentioned in",           40),
    ("Match",                  60),
]

# 1-based column indices, named so the writer reads clearly.
(_NUM_COL, _STATUS_COL, _PAPER_COL, _VENUE_COL,
 _MENTIONS_COL, _MATCH_COL) = 1, 2, 3, 4, 5, 6

# These three columns sit under one merged "Paper specifications" header band on
# header row 1; every other column's header spans both header rows.
_PAPER_SPEC_COLS = (_PAPER_COL, _VENUE_COL, _MENTIONS_COL)
_PAPER_SPEC_LABEL = "Paper specifications"
HEADER_ROWS = 2

# Each paper block is this many rows tall (one labelled field per row).  Only the
# 'Mentioned in' column ever makes a block taller — it grows to list every name;
# the other columns keep their fixed fields and pad the extra rows with blanks.
ROWS_PER_PAPER = 4

_HEADER_FILL = PatternFill("solid", fgColor="2F5496")
_HEADER_FONT = Font(name="Arial", bold=True, size=10, color="FFFFFF")
_DATA_FONT = Font(name="Arial", size=9)
# Rich-text run fonts, so a cell can mix plain text with a linked part — used to
# style only the host in the Domain cell as a link, leaving "Domain: " plain.
_INLINE_PLAIN = InlineFont(rFont="Arial", sz=9, color="000000")
_INLINE_LINK = InlineFont(rFont="Arial", sz=9, color="0563C1", u="single")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_LEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)
# Subtle shade on alternating paper blocks so adjacent papers are easy to tell apart.
_BLOCK_SHADE = "F2F4F8"


def _border():
    edge = Side(style="thin", color="D0D0D0")
    return Border(left=edge, right=edge, top=edge, bottom=edge)


# ---------------------------------------------------------------------------
# Per-column "subrow" builders: one result -> the labelled rows of a column.
# Each returns a list of (text, link_url_or_None).
# ---------------------------------------------------------------------------

def _paper_subrows(r):
    """Four labelled rows for the Paper column: Title / Year / Source / DOI.

    Returns a list of ``(text, link_url_or_None)``; the DOI row links to the
    article (DOI landing page or open-access PDF). 'Source' is the corpus file.
    """
    return [
        (f"Title: {r.get('title') or '(untitled)'}", None),
        (f"Year: {r.get('year') or '—'}", None),
        (f"Source: {r.get('source_file') or '—'}", None),
        (f"DOI: {r.get('doi') or '—'}", r.get("paper_url") or None),
    ]


def _venue_subrows(r):
    """Four labelled rows for the Venue column: Venue / Type / Domain / ISSN.

    When the venue's website is known, the Domain row links to it — and only the
    host text is styled as a link (the "Domain: " label stays plain), via a
    rich-text cell.  (Excel keeps the whole cell clickable, but only the host
    looks like a link.)
    """
    domain = r.get("venue_domain") or "—"
    url = r.get("venue_url") or None
    if url:
        domain_cell = CellRichText([
            TextBlock(_INLINE_PLAIN, "Domain: "),
            TextBlock(_INLINE_LINK, domain),
        ])
    else:
        domain_cell = f"Domain: {domain}"
    return [
        (f"Venue: {r.get('venue') or '—'}", None),
        (f"Type: {r.get('venue_type') or '—'}", None),
        (domain_cell, url),
        (f"ISSN: {r.get('issn') or '—'}", None),
    ]


def _match_subrows(r, provider_labels=None, lists_label=""):
    """Three labelled rows for the Match column.

    For a flagged paper:
      Listed as        — the listed entry that matched; the link opens that
                         entry's OWN website (the listed venue), NOT the paper's
      Found on         — which catalogue, and which SECTION of it the entry sits
                         in. The section, not a category, because "Beall's List —
                         standalone predatory journal" reads as this tool calling
                         the journal predatory, and because naming the section
                         answers the question that row kept provoking: is the
                         journal really listed, or was this inferred from its
                         publisher?
      Why flagged      — how it matched, one idea per sentence, naming the entry
                         first (or the verify-by-hand reason, for review).
    For clean / no_venue / error rows the first row carries a short note and the
    rest are blank.  The plain-English wording comes from descriptions.py.
    """
    status = r.get("status")
    if r.get("cleared_reason"):                 # a hijacked-by-name flag cleared to clean
        note = r["cleared_reason"]
    elif status in _NONMATCH_NOTES:
        # 'no_venue' resolves to whichever of its two causes applied.
        note = _nonmatch_note(r, lists_label)
    elif status == "error":
        note = f"Error: {r.get('matched_on') or 'could not process record'}"
    else:
        why = _on_list_why(r, provider_labels) if status == "on_list" else (
            r.get("review_reason") or "Soft match — verify by hand.")
        listed = r.get("matched_name") or "(matched)"
        listed_url = r.get("matched_url") or None
        if listed_url:
            # A blacklisted venue is on no list but the reader's own, so the
            # sentence that names the destination must not call it Beall-listed.
            #
            # Read the FIRST list, not the whole field: a row matched through
            # several alternate URLs names every list it hit, joined with "; ",
            # and comparing that joined value whole matched nothing — so a
            # counterfeit site was described as "this Beall-listed venue's own
            # website".  The link is the first hit's URL and the joined string is
            # built from the same ordered hits, so the first name is always the
            # list of the entry this sentence is about.
            primary = (split_list_sources(r.get("list_source")) or [""])[0]
            where = ("this hijacked (counterfeit) journal's own website"
                     if primary == "hijacked"
                     else "this venue's own website" if primary == "blacklist"
                     else "this listed venue's own website")
            # Style ONLY the entry name as the link; the "Listed as: " label and
            # the explanation stay plain text (same rich-text approach as the
            # Domain cell).  Excel keeps the whole cell clickable, but the
            # explanation no longer looks like part of the link.
            listed_cell = CellRichText([
                TextBlock(_INLINE_PLAIN, "Listed as: "),
                TextBlock(_INLINE_LINK, listed),
                TextBlock(_INLINE_PLAIN, f"  (the link opens {where}, not the paper)"),
            ])
        else:
            listed_cell = f"Listed as: {listed}"
        return [
            (listed_cell, listed_url),
            (f"Found on: {_listed_on(r, provider_labels)}", None),
            (f"Why flagged: {why}", None),
        ]
    return [(note, None)]


def _mentions_subrows(r):
    """One row per LINKED venue tied to the paper (see match._collect_mentions).

    Each entry is "<Type>: <name>" hyperlinked to the venue's own site; venues
    with no link are omitted, so this can be empty -> a single "—".  Returns ALL
    of them — this is the one column whose block grows to fit, so nothing is
    hidden however many linked venues there are.
    """
    mentions = r.get("mentions") or []
    if not mentions:
        return [("—", None)]
    return [(text, url) for text, url in mentions]


def _data_quality_subrows(r):
    """One row per data-quality warning (empty key -> 'not checked'; none -> OK)."""
    flags = r.get("data_quality")
    if flags is None:
        return [("Not checked — data-quality checks did not run for this record.", None)]
    if not flags:
        return [("OK — no data-quality problems were found in this record.", None)]
    return [(f, None) for f in flags]


# ---------------------------------------------------------------------------
# Block geometry helpers
# ---------------------------------------------------------------------------

def _estimate_row_height(text, width):
    """Row height (px) tall enough for one cell's text to wrap without clipping.

    openpyxl does not auto-fit rows, so a long Title would clip at the default
    height.  Approximate wrapping by dividing the text length by the column's
    character width.
    """
    lines = max(1, -(-len(str(text)) // max(1, width)))      # ceil(len / width)
    return min(140, max(16, lines * 15))


def _row_spans(n_cells, block_rows):
    """Split *block_rows* physical rows among *n_cells* logical cells as evenly
    as possible: ``[(start_offset, span), ...]`` with the spans summing to
    *block_rows*.  One cell yields a single span over the whole block — the
    merged-cell behaviour 'Mentioned in' needs so a lone value fills the block
    instead of leaving blank padding rows beneath it.
    """
    return [(i * block_rows // n_cells,
             (i + 1) * block_rows // n_cells - i * block_rows // n_cells)
            for i in range(n_cells)]


def _fast_merge(ws, min_row, min_col, max_row, max_col):
    """Register a merged range WITHOUT openpyxl's O(n) membership scan.

    ``ws.merge_cells()`` -> ``MultiCellRange.add()`` runs ``if cr not in self``
    on every call, and ``MultiCellRange.__contains__`` scans all existing ranges
    — so merging N ranges is O(N²).  On a 22k-paper workbook that is ~110k merges
    and dominates the write (~93% of save time, pushing it past 15 min).  Our
    ranges are disjoint by construction, so we add the ``CellRange`` straight to
    the underlying ``set`` (O(1)) and skip the scan.  We also skip
    ``_clean_merge_range`` — the covered cells carry no value (only borders /
    shading, set deliberately so the grid renders across the merge), so there is
    nothing to clean.
    """
    ws.merged_cells.ranges.add(
        CellRange(min_col=min_col, min_row=min_row, max_col=max_col, max_row=max_row))


# ---------------------------------------------------------------------------
# Sheet writers
# ---------------------------------------------------------------------------

def _write_results_header(ws, columns, border):
    """Write the two-row header used by the Results / Flagged / Data-quality sheets.

    Header row 1 carries a single merged "Paper specifications" band across the
    Paper / Semantic Scholar venue / Mentioned in columns; row 2 carries those
    three sub-headers.  Every other column's header (``#``, ``Status``, ``Match``
    and any *extra_columns*) spans both rows via a vertical merge.  Cells are
    styled before merging so borders render across the merged ranges.  Freezes
    everything below the header (``A3``).
    """
    spec = set(_PAPER_SPEC_COLS)
    for col, (header, width) in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
        if col in spec:                                  # sub-header on row 2
            c = ws.cell(row=2, column=col, value=header)
            c.font, c.fill, c.alignment, c.border = _HEADER_FONT, _HEADER_FILL, _CENTER, border
            band_cell = ws.cell(row=1, column=col)       # part of the band; style for borders
            band_cell.fill, band_cell.border = _HEADER_FILL, border
        else:                                            # header spans both rows
            c = ws.cell(row=1, column=col, value=header)
            c.font, c.fill, c.alignment, c.border = _HEADER_FONT, _HEADER_FILL, _CENTER, border
            c2 = ws.cell(row=2, column=col)
            c2.fill, c2.border = _HEADER_FILL, border
            ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)

    first, last = _PAPER_SPEC_COLS[0], _PAPER_SPEC_COLS[-1]
    band = ws.cell(row=1, column=first, value=_PAPER_SPEC_LABEL)
    band.font, band.fill, band.alignment, band.border = (
        _HEADER_FONT, _HEADER_FILL, _CENTER, border)
    ws.merge_cells(start_row=1, start_column=first, end_row=1, end_column=last)

    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 22
    ws.freeze_panes = "A3"


def _write_results_sheet(ws, results, extra_columns=None, status_key="status",
                         progress=None, provider_labels=None, lists_label=""):
    """Write the Results sheet: one multi-row block per paper.

    Each paper's block is the taller of ROWS_PER_PAPER and its number of
    'Mentioned in' venues.  Within the block every column's logical cells (from
    the _*_subrows helpers) are spread evenly and MERGED to fill the block, so
    there are never blank padding cells: a column holding a single value becomes
    one cell spanning the whole block (a lone linked venue, or the # / Status
    cells), while 'Mentioned in' grows the block to list every linked venue and
    the other columns keep their fields merged to fit.  Cells are bordered before
    merging so the grid renders across every merged range.

    extra_columns: optional list of ``(header, width, subrows_fn)`` appended
    after the core columns.  ``subrows_fn(result)`` returns
    ``[(text, url_or_None), ...]``.  The LLM pass uses this to add an 'LLM review'
    column to the Flagged-only sheet, and the Data-quality sheet uses it to add a
    'Data-quality issue' column — without duplicating this writer.
    status_key: which result field drives the Status cell + row color.
    progress: optional :class:`Progress` ticked once per paper.  Laying out a
        block of rows per paper is the slow half of writing the workbook, and on
        a big corpus it ran silent for a minute or more after the corpus pass had
        already reported itself finished — which reads as a hang at 100%.
    """
    extra_columns = extra_columns or []
    columns = list(COLUMNS) + [(h, w) for (h, w, _fn) in extra_columns]

    border = _border()
    _write_results_header(ws, columns, border)

    link_font = Font(name="Arial", size=9, color="0563C1", underline="single")
    status_font = Font(name="Arial", size=9, bold=True)
    # Heavier top edge on each paper's first row draws a clear divider between
    # one paper's block and the next, so blocks read as distinct units.
    _edge = Side(style="thin", color="D0D0D0")
    block_top_border = Border(left=_edge, right=_edge, bottom=_edge,
                              top=Side(style="medium", color="7F9BC4"))

    n_core = len(COLUMNS)
    n_cols = len(columns)
    row = HEADER_ROWS + 1                                # data starts below the 2-row header
    for i, r in enumerate(results):
        # Build each column's logical cells, then size the block to the tallest
        # column (only 'Mentioned in' ever exceeds ROWS_PER_PAPER).
        raw_cols = [
            (_PAPER_COL, _paper_subrows(r)),
            (_VENUE_COL, _venue_subrows(r)),
            (_MENTIONS_COL, _mentions_subrows(r)),
            (_MATCH_COL, _match_subrows(r, provider_labels, lists_label)),
        ]
        for k, (_h, _w, fn) in enumerate(extra_columns):
            raw_cols.append((n_core + 1 + k, fn(r)))
        block_rows = max(ROWS_PER_PAPER, max(len(sr) for _, sr in raw_cols))

        top = row
        bottom = top + block_rows - 1
        shade_fill = PatternFill("solid", fgColor=_BLOCK_SHADE) if i % 2 else None
        status = r.get(status_key) or r.get("status", "")
        status_fill = PatternFill("solid", fgColor=STATUS_FILL_COLORS.get(status, "FFFFFF"))
        heights = [16] * block_rows

        # 1) Lay a bordered (and shaded) cell into every physical position first,
        #    so the grid lines render across the merged ranges built in step 3.
        for sub in range(block_rows):
            rr = top + sub
            cell_border = block_top_border if sub == 0 else border   # divider on row 1
            for col in range(1, n_cols + 1):
                cell = ws.cell(row=rr, column=col)
                cell.font, cell.border, cell.alignment = _DATA_FONT, cell_border, _LEFT
                if shade_fill:
                    cell.fill = shade_fill
            ws.cell(row=rr, column=_NUM_COL).alignment = _CENTER
            st = ws.cell(row=rr, column=_STATUS_COL)
            st.font, st.alignment, st.fill = status_font, _CENTER, status_fill

        # 2) # and Status values on the first row, then merge down the block.
        ws.cell(row=top, column=_NUM_COL, value=i + 1)
        ws.cell(row=top, column=_STATUS_COL, value=status)
        if block_rows > 1:
            _fast_merge(ws, top, _NUM_COL, bottom, _NUM_COL)
            _fast_merge(ws, top, _STATUS_COL, bottom, _STATUS_COL)

        # 3) Each content column: place its logical cells, evenly distributed and
        #    merged to fill the block (N values -> N cells, never blank pads).
        for col, subrows in raw_cols:
            width = columns[col - 1][1]
            for (text, url), (start, span) in zip(subrows, _row_spans(len(subrows), block_rows)):
                rr = top + start
                cell = ws.cell(row=rr, column=col, value=text)
                cell.alignment = _LEFT
                cell.border = block_top_border if start == 0 else border
                if shade_fill:
                    cell.fill = shade_fill
                # A rich-text cell carries its own per-run fonts (Domain row), so
                # don't override it with the whole-cell link font.
                if isinstance(text, CellRichText):
                    cell.font = _DATA_FONT
                else:
                    cell.font = link_font if url else _DATA_FONT
                if url:
                    cell.hyperlink = url
                if span > 1:
                    _fast_merge(ws, rr, col, rr + span - 1, col)
                per_row = _estimate_row_height(text, width) / span
                for s in range(span):
                    heights[start + s] = max(heights[start + s], per_row)

        for sub in range(block_rows):
            ws.row_dimensions[top + sub].height = heights[sub]

        row = bottom + 1
        if progress is not None:
            progress.tick()


def _write_summary_sheet(ws, results, snapshot_meta, corpus_files,
                         token_usage=None, llm_stats=None, n_excluded=0,
                         provider_labels=None):
    """Write counts + run metadata so results are interpretable.

    token_usage / llm_stats are populated only when the optional LLM second
    pass (bealls_llm_check.py) produced the workbook; they add an 'LLM second
    pass' section with the token counter and what the LLM changed.

    *n_excluded* is how many papers the whitelist kept out of the run.  They are
    in no sheet — that is what a whitelist now does — so this is the one place
    the workbook can account for the difference between the corpus handed in and
    the papers reported on, and it is written even when it is zero.
    """
    border = _border()
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 60

    def header(row, a, b):
        for col, val in ((1, a), (2, b)):
            c = ws.cell(row=row, column=col, value=val)
            c.font, c.fill, c.alignment, c.border = (
                _HEADER_FONT, _HEADER_FILL, _CENTER, border
            )

    def kv(row, label, value):
        ws.cell(row=row, column=1, value=label).font = Font(name="Arial", size=10, bold=True)
        c = ws.cell(row=row, column=2, value=value)
        c.font = Font(name="Arial", size=10)
        c.alignment = _LEFT

    provider_labels = provider_labels or {}
    provider_meta_rows = (snapshot_meta or {}).get("providers") or []
    total = len(results)
    status_counts = {s: 0 for s in STATUS_FILL_COLORS}
    list_counts = {}
    signal_counts = {}        # matched_on -> count, over flagged rows only
    for r in results:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        if r["status"] in ("on_list", "review"):
            # Count each list the paper matched, not the field as one string.  A
            # row matched through several alternate URLs names them joined with
            # "; ", and keying on that produced a bucket of its own — a Summary
            # line reading "predatory publisher; standalone predatory journal"
            # beside the single-list lines, whose number belonged to neither.
            # The renderer below already splits the key it is handed
            # (descriptions._list_plain), so only the counter was out of step.
            # A paper on two lists now counts once under each, which means these
            # figures can sum to more than the flagged total.
            provs = split_list_sources(r.get("provider")) or [""]
            for i, src in enumerate(split_list_sources(r["list_source"])):
                # Pair each list with the catalogue it came from; both fields are
                # joined in the same order, so index i pairs them correctly.
                prov = provs[i] if i < len(provs) else provs[-1]
                list_counts[(prov, src)] = list_counts.get((prov, src), 0) + 1
            mo = r.get("matched_on") or "—"
            signal_counts[mo] = signal_counts.get(mo, 0) + 1

    row = 1
    header(row, "Metric", "Value"); row += 1
    kv(row, "Total papers checked", total); row += 1
    kv(row, "Flagged, high confidence (on_list)", status_counts.get("on_list", 0)); row += 1
    kv(row, "Needs review (soft match)", status_counts.get("review", 0)); row += 1
    kv(row, "Legitimate — on none of the lists used (clean)",
       status_counts.get("clean", 0)); row += 1
    kv(row, "No venue (preprint / no info)", status_counts.get("no_venue", 0)); row += 1
    kv(row, "Errors", status_counts.get("error", 0)); row += 1
    # Below the verdicts, and worded as a count of papers rather than a verdict,
    # because it is not one: these papers were never examined.  Every figure
    # above is out of "Total papers checked"; this one is outside it.
    kv(row, "Excluded by the whitelist (not examined, in no sheet)", n_excluded); row += 1
    kv(row, "Records with data-quality warnings",
       sum(1 for r in results if r.get("data_quality"))); row += 2

    # Breakdown by HOW each flagged paper matched (the signal now folded into the
    # 'Why flagged' line), e.g. how many were 'an alternate web link points to a
    # listed site'.
    header(row, "Flagged papers by how the venue matched", "Count"); row += 1
    for mo in sorted(signal_counts, key=lambda k: (-signal_counts[k], k)):
        kv(row, f"  {_signal_plain(mo)}  [{mo}]", signal_counts[mo]); row += 1
    if not signal_counts:
        kv(row, "  (none)", 0); row += 1
    row += 1

    # Breakdown by which Beall list each flagged paper matched.
    # A paper on two lists is counted under each, so these can sum to more than
    # the flagged total; the header says so rather than leaving it to be noticed.
    header(row, "Flagged papers by list (a paper on two lists counts under each)",
           "Count"); row += 1
    for key in sorted(list_counts, key=lambda k: (-list_counts[k], k)):
        prov, src = key
        label = _list_plain(src)
        if prov:
            label = f"{provider_labels.get(prov, prov)} — {label}"
        kv(row, f"  {label}", list_counts[key]); row += 1
    if not list_counts:
        kv(row, "  (none)", 0); row += 1
    row += 1

    # LLM review pass — only present when bealls_llm_check.py built the workbook.
    # It assesses ONLY the 'review' papers and writes a recommendation into the
    # Flagged-only sheet's 'LLM review' column.
    if token_usage is not None or llm_stats:
        llm_stats = llm_stats or {}
        header(row, "LLM review (review papers only)", "Value"); row += 1
        if llm_stats.get("provider"):
            kv(row, "LLM provider", llm_stats["provider"]); row += 1
        if llm_stats.get("model"):
            kv(row, "Model / deployment", llm_stats["model"]); row += 1
        if "review_venues_checked" in llm_stats:
            kv(row, "Distinct 'review' venues assessed", llm_stats["review_venues_checked"]); row += 1
        if "llm_yes" in llm_stats:
            kv(row, "  -> recommend EXCLUDE (predatory)", llm_stats["llm_yes"]); row += 1
        if "llm_no" in llm_stats:
            kv(row, "  -> recommend KEEP (legitimate)", llm_stats["llm_no"]); row += 1
        if "llm_uncertain" in llm_stats:
            kv(row, "  -> uncertain (check by hand)", llm_stats["llm_uncertain"]); row += 1
        if token_usage is not None:
            kv(row, "LLM requests", token_usage.request_count); row += 1
            kv(row, "Input tokens", f"{token_usage.prompt_tokens:,}"); row += 1
            kv(row, "Output tokens", f"{token_usage.completion_tokens:,}"); row += 1
            kv(row, "Total tokens", f"{token_usage.total_tokens:,}"); row += 1
        row += 1

    header(row, "Reference lists used", "Entries / as of"); row += 1
    if provider_meta_rows:
        for p in provider_meta_rows:
            kv(row, f"  {p['label']}",
               f"{p['total_entries']} entries, as of {(p['scraped_utc'] or '?')[:10]}")
            row += 1
    else:
        kv(row, "  (none recorded)", ""); row += 1
    row += 1

    header(row, "Run metadata", ""); row += 1
    kv(row, "Snapshot scraped (UTC)", snapshot_meta.get("scraped_utc", "")); row += 1
    kv(row, "Snapshot entries", snapshot_meta.get("total_entries", "")); row += 1
    kv(row, "Snapshot source", snapshot_meta.get("source", "")); row += 1
    kv(row, "Corpus files", ", ".join(corpus_files)); row += 1
    kv(row, "Check run (local)", datetime.now().strftime("%Y-%m-%d %H:%M:%S")); row += 2

    header(row, "Caveat", ""); row += 1
    kv(row, "Interpretation", snapshot_meta.get("note", "")); row += 1
    ws.cell(row=row, column=1,
            value="'on_list' = exact domain/ISSN/name match. 'review' = fuzzy or "
                  "soft match — verify by hand before trusting.").font = Font(
        name="Arial", size=9, italic=True)


def _write_flagged_sheet(ws, results, extra_columns=None, progress=None,
                         provider_labels=None, lists_label=""):
    """Write only the on_list + review rows (the actionable subset)."""
    flagged = [r for r in results if r["status"] in ("on_list", "review")]
    # Sort: on_list first, then by list source, then title.
    order = {"on_list": 0, "review": 1}
    flagged.sort(key=lambda r: (order.get(r["status"], 9), r["list_source"], r["title"]))
    if progress is not None:
        progress.add_total(len(flagged))
    _write_results_sheet(ws, flagged, extra_columns=extra_columns, progress=progress,
                         provider_labels=provider_labels, lists_label=lists_label)
    return len(flagged)


def _write_legitimate_sheet(ws, results, progress=None,
                            provider_labels=None, lists_label=""):
    """Write only the papers that cleared every list the run consulted.

    The complement of 'Flagged only', and the sheet a reader can act on directly:
    these are the papers a corpus screen would keep.

    Deliberately ``clean`` alone.  A ``no_venue`` paper — a preprint, or a record
    with no venue fields — was never compared against any list, so calling it
    legitimate would claim a check that did not happen.  Those papers keep their
    own row on the Results sheet, where the Match column now says which of the
    two applied.  ``review`` is excluded for the obvious reason: it is the set
    that still needs a human.
    """
    legit = [r for r in results if r["status"] == "clean"]
    legit.sort(key=lambda r: (r.get("venue") or "", r.get("title") or ""))
    if progress is not None:
        progress.add_total(len(legit))
    _write_results_sheet(ws, legit, progress=progress,
                         provider_labels=provider_labels, lists_label=lists_label)
    return len(legit)


def _write_legend_sheet(ws):
    """Render the Legend tab from the declarative ``_LEGEND_SECTIONS`` (descriptions.py)."""
    border = _border()
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 95
    bold = Font(name="Arial", size=10, bold=True)
    data = Font(name="Arial", size=10)
    section_font = Font(name="Arial", size=12, bold=True, color="2F5496")

    ws.cell(row=1, column=1, value=_LEGEND_TITLE).font = Font(
        name="Arial", size=14, bold=True)
    row = 3

    for sec in _LEGEND_SECTIONS:
        ws.cell(row=row, column=1, value=sec["title"]).font = section_font
        row += 1

        if sec["headers"] is None:                      # caveats: bullet lines
            for (text,) in sec["rows"]:
                cell = ws.cell(row=row, column=1, value="• " + text)
                cell.font, cell.alignment = data, _LEFT
                ws.merge_cells(start_row=row, start_column=1,
                               end_row=row, end_column=3)
                row += 1
            row += 1
            continue

        for col, header in enumerate(sec["headers"], 1):   # header row
            cell = ws.cell(row=row, column=col, value=header)
            cell.font, cell.fill, cell.alignment, cell.border = (
                _HEADER_FONT, _HEADER_FILL, _CENTER, border)
        row += 1

        for cells in sec["rows"]:                          # data rows
            for col, val in enumerate(cells, 1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.font = bold if col == 1 else data
                cell.border, cell.alignment = border, _LEFT
                if col == 1 and sec.get("swatch"):
                    cell.fill = PatternFill(
                        "solid", fgColor=STATUS_FILL_COLORS.get(val, "FFFFFF"))
            row += 1
        row += 1


def save_workbook(results, snapshot_meta, corpus_files, *,
                  flagged_extra_columns=None, token_usage=None, llm_stats=None,
                  results_filename=None, n_excluded=0):
    """Build and save the combined workbook; return ``(path, n_flagged)``.

    The keyword-only args let the optional LLM pass (bealls_llm_check.py) reuse
    this exact writer: *flagged_extra_columns* adds extra column(s) to the
    Flagged-only sheet only (the LLM pass uses it for the 'LLM review' column, so
    the verdict sits with the papers a human actually reviews); *token_usage* /
    *llm_stats* feed the Summary's 'LLM review' section; *results_filename*
    writes to a different file than the default.

    Each sheet announces itself, and the two big ones tick per paper: writing the
    workbook for a large corpus takes minutes, and it used to happen in complete
    silence AFTER the corpus pass had reported "100%", so the run looked finished
    and hung at the same time.
    """
    # Where the catalogue names come from: the run's own metadata, so the report
    # cannot disagree with what was actually loaded.
    provider_meta = (snapshot_meta or {}).get("providers") or []
    provider_labels = {p["provider"]: p["label"] for p in provider_meta}
    lists_label = ", ".join(p["label"] for p in provider_meta)

    wb = openpyxl.Workbook()
    ws_results = wb.active
    ws_results.title = "Results"
    print(f"Writing workbook: Results sheet ({len(results)} papers) ...", flush=True)
    sheet_progress = Progress("papers")
    sheet_progress.add_total(len(results))
    _write_results_sheet(ws_results, results, progress=sheet_progress,
                         provider_labels=provider_labels, lists_label=lists_label)
    sheet_progress.close()

    ws_flagged = wb.create_sheet("Flagged only")
    print("  Flagged only sheet ...", flush=True)
    flagged_progress = Progress("papers")      # its own total, counted inside the writer
    n_flagged = _write_flagged_sheet(ws_flagged, results,
                                     extra_columns=flagged_extra_columns,
                                     progress=flagged_progress,
                                     provider_labels=provider_labels,
                                     lists_label=lists_label)
    flagged_progress.close()

    # The complement: the papers that cleared every list consulted.
    ws_legit = wb.create_sheet("Legitimate only")
    print("  Legitimate only sheet ...", flush=True)
    legit_progress = Progress("papers")
    n_legit = _write_legitimate_sheet(ws_legit, results, progress=legit_progress,
                                      provider_labels=provider_labels,
                                      lists_label=lists_label)
    legit_progress.close()

    # Its own sheet (not a column): only the papers whose Semantic Scholar record
    # has a data-quality issue, each shown with the specific issue(s).
    dq_flagged = [r for r in results if r.get("data_quality")]
    if dq_flagged:
        ws_dq = wb.create_sheet("Data quality")
        print(f"  Data quality sheet ({len(dq_flagged)} papers) ...", flush=True)
        dq_progress = Progress("papers")
        dq_progress.add_total(len(dq_flagged))
        _write_results_sheet(ws_dq, dq_flagged,
                             extra_columns=[("Data-quality issue", 60, _data_quality_subrows)],
                             progress=dq_progress, provider_labels=provider_labels,
                             lists_label=lists_label)
        dq_progress.close()

    ws_summary = wb.create_sheet("Summary")
    _write_summary_sheet(ws_summary, results, snapshot_meta, corpus_files,
                         token_usage=token_usage, llm_stats=llm_stats,
                         n_excluded=n_excluded, provider_labels=provider_labels)

    ws_legend = wb.create_sheet("Legend")
    _write_legend_sheet(ws_legend)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = results_filename or RESULTS_FILENAME
    out_path = RESULTS_DIR / filename
    # openpyxl serialises the whole workbook in one call we cannot tick inside,
    # so the wait is announced before it starts rather than explained after it.
    print(f"  Summary + Legend done; saving {filename} "
          f"(a large corpus can take a minute) ...", flush=True)
    try:
        wb.save(out_path)
    except PermissionError:
        # The target is almost certainly open in Excel (Windows locks it).
        # Fall back to a timestamped name so a run is never lost.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"{Path(filename).stem}_{stamp}.xlsx"
        wb.save(out_path)
        print(f"  (default file was locked — saved to {out_path.name} instead)")
    return out_path, n_flagged
