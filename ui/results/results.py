"""What comes back from a run, on screen.

The workbook or the CSV, previewed rather than only offered: a download button
alone asks someone to open a file to find out whether the run did what they
meant.  Also the chips the rest of the page is labelled with, and the one rule
that keeps them readable — the status fills are the workbook's own, so the text
laid over them is chosen by luminance rather than by a hand-kept list.
"""

import html
import io
import sys

import openpyxl
import streamlit as st

from pathlib import Path

# Flat imports across ui/'s feature folders — `import styles`, not
# `from ui.styles import styles`.  ui/ is not a package (Streamlit runs app.py as
# a script), so every folder is put on the path first.  Repeated per module
# rather than shared, so each still imports when it is the thing being run: from
# a shell, or by anything that never touched app.py.  Same shim the check
# engine's own foldered modules use — see src/bealls_list_check/*/.
_UI = Path(__file__).resolve().parent.parent
for _p in [_UI, *(d for d in _UI.iterdir() if d.is_dir() and any(d.glob("*.py")))]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import runners

# In-process validator for the live "Input data quality" panel. The two path
# inserts are runners' own layout knowledge, reused rather than restated.
sys.path.insert(0, str(runners.RC))           # src/  (for the `common` package)
sys.path.insert(0, str(runners._BEALLS))      # bealls_list_check/
try:
    from common import input_quality           # the shared, field-aware validator
    _VALIDATORS_OK = True
except Exception:
    _VALIDATORS_OK = False


_STATUS_ORDER = ("on_list", "review", "clean", "no_venue", "error")


def _chip(text, tone="mute", *, title=None, flow=False):
    """A small pill in one of the palette's tones.

    *tone* is ``mute``, ``ok`` or ``warn`` — a class rather than a colour, so the
    pill picks up the light or dark value of that tone from :data:`_TOKENS`
    instead of carrying a hex that only works against one background.

    *title* becomes the browser's hover text — the sentence of detail that does
    not fit inside a pill — and marks the pill with the help cursor so there is
    something to invite the hover.  *flow* adds the trailing margins wanted when
    several pills wrap across a line; a pill alone in a table cell wants none,
    because the margin would push it off the cell's vertical centre.
    """
    cls = f"bl-chip bl-{tone}" + (" bl-flow" if flow else "")
    if not title:
        return f"<span class='{cls}'>{text}</span>"
    return (f"<span class='{cls} bl-help' "
            f"title='{html.escape(title, quote=True)}'>{text}</span>")


def readable_on(fill: str) -> str:
    """Slate-900 or white — whichever reads on the ``RRGGBB`` *fill*.

    The status fills are Excel's and are not ours to re-tone, but they are not
    uniform either: most are pale and want dark text, while ``error`` is a deep
    red that wants white.  Decided by WCAG relative luminance rather than by a
    hand-kept list, so a change to ``STATUS_FILL_COLORS`` cannot leave a chip
    unreadable.  The threshold is where the two contrast ratios meet.
    """
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(int(fill[i:i + 2], 16)) for i in (0, 2, 4))
    return "#0f172a" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.204 else "#ffffff"


def _status_chip(text, status, *, flow=False):
    """A pill in a *status*'s canonical colour, from ``STATUS_FILL_COLORS``.

    Separate from :func:`_chip` on purpose.  Those colours are the workbook's —
    the single source of truth is ``config.py`` — so they must not be re-toned
    for a dark background, or the chips would stop matching the file they
    describe.  Only the text on them is chosen here.
    """
    fill = runners.STATUS_FILL_COLORS.get(status, "FFFFFF")
    cls = "bl-chip" + (" bl-flow" if flow else "")
    return (f"<span class='{cls}' style='background:#{fill};"
            f"color:{readable_on(fill)};'>{text}</span>")


def _status_summary(rows):
    """Coloured count-per-status chips (canonical colours), most-flagged first."""
    counts = {}
    for r in rows:
        counts[r["Status"]] = counts.get(r["Status"], 0) + 1
    ordered = [s for s in _STATUS_ORDER if s in counts] + \
              [s for s in counts if s not in _STATUS_ORDER]
    st.markdown("".join(_status_chip(f"{s}: <b>{counts[s]}</b>", s, flow=True)
                        for s in ordered), unsafe_allow_html=True)


# Both halves of the panel are keyed on *token* — the name Step 2 gives the
# corpus it loaded (see app._parse_corpus) — because both are a pass over every
# record and Streamlit reruns this script on every interaction.  Measured on an
# 18,482-paper export: 7.5s to validate and 3.1s to build the workbook, on every
# keystroke, while the panel that shows them is collapsed.
#
# The workbook is the one that surprises: `st.download_button` takes its bytes
# as an argument, so the file has to exist before the button can be drawn, and
# an expander renders its contents whether or not it is open.  Nothing was
# waiting for that workbook — it was being built in case someone asked.
#
# `cache_resource` for the flags, because they hold the paper dicts themselves
# and cache_data would pickle and copy all of them on every access;
# `cache_data` for the workbook, which is 1.6 MB of bytes and is copied for the
# cost of a memcpy.  One entry each: the corpus on screen, and no other.
@st.cache_resource(show_spinner=False, max_entries=1)
def _validated(_items, token):
    """What the offline validator says about the corpus *token* names."""
    return input_quality.validate_papers(_items)


@st.cache_data(show_spinner=False, max_entries=1)
def _quality_xlsx(_items, _flagged, token):
    """The quality report for the corpus *token* names, as .xlsx bytes."""
    return runners.build_input_quality_xlsx(_items, _flagged)


def show_input_quality(items, token, key, into=None):
    """Up-front panel: validate the Semantic Scholar metadata, offer it as Excel.

    *token* names the corpus, and is what both halves are cached on; see above.

    *into* is the container Step 2 reserved for this panel.  It exists because
    the panel is written after the steps that appear below it — the validation is
    the slowest thing on the page, and it used to hold their delivery up — so the
    place it occupies and the moment it is filled are two different things.
    """
    if not _VALIDATORS_OK:
        return
    with (into if into is not None else st.container()):
        _input_quality_body(items, token, key)


def _input_quality_body(items, token, key):
    """The panel itself, in whatever container the caller has opened."""
    try:
        # Named, because on a corpus this has not seen the wait is real and the
        # panel is the only thing that can account for it.
        with st.spinner(f"Checking the metadata of {len(items)} record(s)…"):
            flagged = _validated(items, token)           # offline validator
    except Exception as exc:
        st.caption(f"(input validation unavailable: {exc})")
        return
    # Collapsed, always.  This panel reports on the *metadata that was supplied*,
    # not on anything the tool has done, and it is normal for a real corpus to
    # have some — the example corpus is deliberately one of those, because record
    # 6 carries no venue at all.  Opening itself and leading with a warning made
    # a green "Loaded 8 paper(s) — ready to run" be immediately contradicted by
    # what looked like an error.  The count is in the label, which is the part
    # worth seeing; the detail is one click away for whoever wants it.
    icon = "⚠️" if flagged else "✅"
    label = (f"{icon} Input data quality — {len(flagged)} of {len(items)} record(s) flagged"
             if flagged else
             f"✅ Input data quality — nothing flagged in {len(items)} record(s)")
    with st.expander(label, expanded=False):
        st.caption("An offline look at the Semantic Scholar metadata *before* the check runs. "
                   "A flag means a record is missing something the venue match relies on, so "
                   "its verdict may be weaker — it does not stop the run and is not an error.")
        st.download_button("⬇️ Download report (.xlsx)",
                           data=_quality_xlsx(items, flagged, token),
                           file_name="input_data_quality.xlsx", key=f"dqdl_{key}",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if not flagged:
            st.caption("No issues found by the offline checks. (Not a guarantee of correctness; "
                       "deeper checks run during the check itself and show in the run log.)")
        for i, p, flags in flagged[:5]:
            st.markdown(f"**#{i}** {(p.get('title') or '?')[:90]} — " + "; ".join(flags))
        if len(flagged) > 5:
            st.caption(f"…and {len(flagged) - 5} more — download the report above for the full list.")


def _preview(xlsx_bytes: bytes, max_rows: int = 40):
    """Show sheet names + the first sheet's first rows as a table."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception as e:                      # never let a preview crash the page
        st.caption(f"(Preview unavailable: {e})")
        return
    st.caption("Sheets: " + ", ".join(wb.sheetnames))
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return
    headers = [str(h) if h is not None else f"col{i}" for i, h in enumerate(rows[0])]
    # Everything as text: a sheet column here mixes numbers, strings and the
    # blanks left by merged cells, which Arrow cannot type (it logs a traceback
    # and falls back).  A preview of a formatted workbook is text anyway.
    data = [dict(zip(headers, [("" if v is None else str(v)) for v in r]))
            for r in rows[1:max_rows + 1]]
    st.dataframe(data)
    if len(rows) > max_rows + 1:
        st.caption(f"(showing first {max_rows} of {len(rows) - 1} rows — download for the full sheet)")


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def render_result(res: runners.RunResult, checker: runners.Checker):
    if res.ok:
        st.success(f"✅ Done — {checker.title} finished. Your Excel is ready below.")
        st.download_button(f"⬇️ Download {res.output_path.name}", data=res.output_bytes,
                           file_name=res.output_path.name, key=f"dl_{checker.id}",
                           mime=_XLSX_MIME)
        _preview(res.output_bytes)
        # The same button again, under the preview.  The preview is the thing
        # that makes anyone want the file — it shows 40 rows of the first of five
        # sheets, and its own caption ends by saying to download for the rest —
        # and by the time it has been read the button above it is off the top of
        # the screen.  Two buttons for one file is a cost worth paying when the
        # alternative is scrolling back up to a control you have already passed.
        st.download_button(f"⬇️ Download the full {res.output_path.name}",
                           data=res.output_bytes, file_name=res.output_path.name,
                           key=f"dl_{checker.id}_after_preview", type="primary",
                           mime=_XLSX_MIME)
    else:
        st.error(f"The checker exited with code {res.returncode} and produced no output file. "
                 "See the run log below.")
    with st.expander("Run log (incl. the input data-quality report)", expanded=not res.ok):
        if res.stdout:
            st.text(res.stdout[-8000:])
        if res.stderr:
            st.caption("stderr")
            st.text(res.stderr[-4000:])


def _csv_status_preview(rows, max_rows=100):
    """Preview the CSV with each Status cell shaded its canonical colour."""
    preview = rows[:max_rows]
    try:
        import pandas as pd
        df = pd.DataFrame(preview, columns=["SS-ID", "DOI", "Status"])

        def _bg(val):
            hexc = runners.STATUS_FILL_COLORS.get(val)
            # The text colour has to be set alongside the fill.  These are pale
            # Excel fills, and the dark theme's own table text is near-white on
            # them; `error` is the mirror case, a deep red under dark text.
            return (f"background-color: #{hexc}; color: {readable_on(hexc)}"
                    if hexc else "")

        styler = df.style
        # pandas >= 2.1 renamed Styler.applymap -> Styler.map.
        styler = (styler.map(_bg, subset=["Status"]) if hasattr(styler, "map")
                  else styler.applymap(_bg, subset=["Status"]))
        st.dataframe(styler)
    except Exception:
        st.dataframe(preview)                             # plain fallback
    if len(rows) > max_rows:
        st.caption(f"(showing first {max_rows} of {len(rows)} rows — download for the full CSV)")


def render_csv_result(res: runners.BatchCsvResult):
    if res.ok:
        st.success(f"✅ Done — {len(res.rows)} record(s) classified. "
                   "Download the CSV (SS-ID, DOI, Status) below.")
        _status_summary(res.rows)
        # Built once and handed to both pairs of buttons.  `st.download_button`
        # takes its bytes as an argument, so a second button naming the same file
        # would otherwise build the workbook a second time on every rerun — the
        # same trap the input-quality report was in.
        status_xlsx = runners.build_status_xlsx(res.rows)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇️ Download CSV (SS-ID, DOI, Status)", data=res.csv_bytes,
                               file_name="bealls_status.csv", key="dl_csv", mime="text/csv")
        with c2:
            st.download_button("⬇️ Download colour-coded Excel", key="dl_csv_xlsx",
                               data=status_xlsx,
                               file_name="bealls_status.xlsx", mime=_XLSX_MIME)
        _csv_status_preview(res.rows)
        # Under the preview as well, for the reason given in render_result: this
        # preview stops at 100 rows and says so, and the pair of buttons that
        # answer that is above everything it just showed.
        b1, b2 = st.columns(2)
        with b1:
            st.download_button("⬇️ Download the full CSV", data=res.csv_bytes,
                               file_name="bealls_status.csv", key="dl_csv_after_preview",
                               mime="text/csv")
        with b2:
            st.download_button("⬇️ Download the full Excel", key="dl_csv_xlsx_after_preview",
                               data=status_xlsx, file_name="bealls_status.xlsx",
                               type="primary", mime=_XLSX_MIME)
    else:
        st.error(f"The batch check exited with code {res.returncode} and produced no CSV. "
                 "See the run log below.")
    with st.expander("Run log", expanded=not res.ok):
        if res.stdout:
            st.text(res.stdout[-8000:])
        if res.stderr:
            st.caption("stderr")
            st.text(res.stderr[-4000:])
