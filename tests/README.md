# Tests

A regression suite for the deterministic half of the check: **matcher → Excel
writer**. It exists so that a change to matching or to wording is visible cell by
cell, rather than being discovered in a spreadsheet weeks later.

```bash
python -m unittest discover -s tests -t tests -v
```

No dependencies beyond those the project already requires. The suite uses the
standard library's `unittest` rather than pytest deliberately: adding a test
runner to [`requirements.txt`](../requirements.txt) would change what every user
of the application installs, for the benefit of developers alone.

## What runs

| File | Contents |
|---|---|
| [`test_report_golden.py`](test_report_golden.py) | The fixture, the workbook dump helper, and both test classes. |
| `golden/report_baseline.json` | The recorded baseline. Regenerated deliberately, never automatically. |

### `TestMatcherVerdicts` — the rules, stated as rules

Asserts the verdict each fixture paper must reach, so that a matcher regression
names the rule it broke instead of surfacing as an unexplained cell diff. It also
pins two properties of the fixture itself: that all five list categories are
exercised, and that the joined-`list_source` case is still present — several of
the defects this suite guards against are reachable only through a joined value.

### `TestWorkbookGolden` — the rendered output, cell by cell

Compares a canonical dump of the generated workbook against the baseline. The
comparison is **cell-level, not byte-level**: two saves of an identical workbook
differ in their bytes, because every ZIP member carries a wall-clock timestamp
and `docProps/core.xml` carries a creation date. Byte-identity is therefore not
an achievable acceptance test, and was never the right one.

## What the baseline protects

For all five sheets — Results, Flagged only, Data quality, Summary, Legend:

- **Sheet set and order.** A sheet that disappears, or is renamed, or moves.
- **Dimensions.** `max_row` and `max_column` per sheet.
- **Every non-empty cell**, by coordinate: its value, its solid fill colour, whether
  it is bold, and its hyperlink target.
- **Merge ranges** — 60 of them on the Results sheet alone. This is what pins the
  per-paper **block geometry**: the four-row block, the way each column's fields
  are spread across it, and the way *Mentioned in* grows a block to fit.
- **Row heights and column widths** — the layout, including the wrap estimates
  that `_estimate_row_height` produces.
- **Status colours**, so that the mapping in `config.STATUS_FILL_COLORS` cannot be
  silently dropped or altered.
- The flagged-row count, and each paper's `status`, `list_source` and `matched_on`.

The fixture covers, one paper per case: all four `on_list` signals (domain,
subdomain, ISSN, and an exact name against an entry with no website); an
uncorroborated name match; a hijacked entry matched by name; both weak lists
(vanity press, fake metrics); a fuzzy name; **a paper flagged by two different
lists at once, so `list_source` is `'; '`-joined**; a clean paper; a preprint; a
record with no venue at all; and a malformed record that becomes an `error` row.

## What it does *not* cover

- **Anything network-bound.** `resolve.py` (Crossref DOI resolution) and the
  Crossref branch of `data_quality.py` are excluded by construction: the test
  calls `match_paper` directly and supplies the `data_quality` field itself, so a
  run is fully offline and reproducible. The DOI resolvers have no coverage here.
- **The real snapshot.** The fixture is synthetic, so refreshing
  `data/bealls_snapshot.json` cannot break this suite. Every host in it ends in
  `.test`, a reserved TLD (RFC 2606) that can never resolve.
- **The CLI, the batch CSV, the LLM pass, and the Streamlit UI.**

## Changing the baseline

A failure is the suite doing its job. Read the diff first: it names the sheet and
the cell.

If the change was intended, re-record and **explain each moved cell in the commit
message**:

```bash
python tests/test_report_golden.py --update
```

The one value normalised out of the dump is the Summary sheet's *Check run
(local)* timestamp, which `report.py` fills from `datetime.now()`.
