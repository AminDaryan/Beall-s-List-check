"""Adapter layer between the Streamlit UI and the Beall's List check scripts.

Each check is a standalone script that reads its corpus from a folder pointed at
by ``BEALLS_CORPUS_DIR`` and writes an Excel file to a fixed location.  Rather
than import the scripts in-process (they reuse module names like
``config``/``match`` that would collide), we run each as a **subprocess** with
the uploaded corpus injected via an environment variable, then read back the
Excel it produced.

This module is import-safe (no Streamlit) so it can be unit-tested on its own.
"""

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
RC = SRC                          # the dir that holds the ``common`` package (app.py adds it to sys.path)
_BEALLS = SRC / "bealls_list_check"
_BATCH_SCRIPT = _BEALLS / "batch_check" / "batch_check.py"

# Canonical status -> fill colour. Single source of truth is config.py
# (STATUS_FILL_COLORS), imported when the path shim allows; the literal is a
# fallback so this module stays import-safe on its own. Used by the coloured
# status preview and the colour-coded .xlsx the UI's batch mode offers.
try:
    if str(_BEALLS) not in sys.path:
        sys.path.insert(0, str(_BEALLS))
    from config import STATUS_FILL_COLORS
except Exception:
    STATUS_FILL_COLORS = {
        "on_list": "FFC7CE", "review": "FFD79A", "clean": "C6EFCE",
        "no_venue": "D9D9D9", "error": "C00000",
    }


@dataclass(frozen=True)
class Checker:
    id: str
    label: str              # full name, as used in the documentation
    script: Path            # script to run as a subprocess
    output: Path            # Excel it writes (we also scan its folder for fresher files)
    needs_llm: bool = False
    note: str = ""
    description: str = ""        # plain-language "what this check answers", shown in the UI
    kind: str = "xlsx"          # "xlsx" -> run_checker (Excel); "csv" -> run_batch_csv
    # Presentation only — the comparison table the UI offers in Step 1. Kept here
    # so the three checks are described in exactly one place. Each ``*_label`` is
    # the chip shown in its column; the matching ``*_note`` is its hover text,
    # which carries the detail that would not fit in a chip.
    icon: str = "🔎"
    short_label: str = ""        # table heading and run messages; falls back to *label*
    tagline: str = ""            # one line under the heading: how the check works
    cost_label: str = ""         # what running it costs
    cost_note: str = ""
    records_label: str = ""      # what it does with records that repeat in the corpus
    records_note: str = ""
    output_label: str = ""       # what the check hands back
    output_note: str = ""

    @property
    def title(self) -> str:
        return self.short_label or self.label


# The two workbook checks share their cost and record handling, so those strings
# are named once rather than repeated per check.
_FREE = "Free"
_FREE_NOTE = ("No API keys and no tokens — the whole check runs offline against the "
              "scraped Beall's List snapshot. Crossref lookups need the internet but "
              "are optional and free.")
_DEDUPED = "Merged"
_DEDUPED_NOTE = ("A paper that appears more than once in the corpus is reported once: "
                 "records are deduplicated on paperId, falling back to the title. The "
                 "workbook therefore has fewer entries than the corpus when it repeats.")

CHECKERS: List[Checker] = [
    Checker("bealls.deterministic", "Fast check — rules only (no LLM)",
            _BEALLS / "bealls_list_check.py",
            _BEALLS / "results" / "bealls_list_results.xlsx",
            icon="⚡", short_label="Fast check", tagline="Rules only — no LLM",
            cost_label=_FREE, cost_note=_FREE_NOTE,
            records_label=_DEDUPED, records_note=_DEDUPED_NOTE,
            output_label="Excel workbook",
            output_note="bealls_list_results.xlsx — the five-sheet report, in which each "
                        "paper occupies a block of rows rather than a single row.",
            description="Is the paper's journal or publisher on Beall's List of "
                        "potentially predatory venues?"),
    Checker("bealls.llm", "Thorough check — rules + LLM over the review papers",
            _BEALLS / "bealls_llm_check.py",
            _BEALLS / "results" / "bealls_llm_results.xlsx",
            needs_llm=True,
            icon="🧠", short_label="Thorough check", tagline="Rules, then an LLM pass",
            cost_label="LLM tokens",
            cost_note="Needs provider keys in the repository's .env, and spends tokens: "
                      "one LLM call per distinct venue that the rules left as review.",
            records_label=_DEDUPED, records_note=_DEDUPED_NOTE,
            output_label="Excel workbook",
            output_note="bealls_llm_results.xlsx — the same five-sheet report plus an "
                        "LLM review column on the 'Flagged only' sheet.",
            description="The fast check, then a keep / exclude / uncertain recommendation "
                        "from an LLM for every paper that needs review."),
    Checker("bealls.batch_csv", "Batch CSV — SS-ID, DOI, Status (for server integration)",
            _BATCH_SCRIPT,
            _BEALLS / "batch_check",          # unused for kind="csv" (no fixed output file)
            kind="csv",
            icon="📄", short_label="Batch CSV", tagline="One row per input record",
            cost_label=_FREE, cost_note=_FREE_NOTE,
            records_label="All kept",
            records_note="One CSV row per record you supply, in the order you supplied "
                         "them — nothing is merged, so a corpus that repeats a paper "
                         "yields more rows than the workbook. A whitelist is the one "
                         "thing that removes rows: a paper it excludes is not checked, "
                         "so it is not a row.",
            output_label="CSV + Excel",
            output_note="bealls_status.csv — three columns, SS-ID, DOI and Status — plus "
                        "the same three columns as a colour-coded bealls_status.xlsx.",
            description="SS-ID, DOI and the verdict as a machine-readable CSV for scripts "
                        "and servers, with a colour-coded Excel twin."),
]

CHECKERS_BY_ID = {c.id: c for c in CHECKERS}


@dataclass
class RunResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    output_path: Optional[Path]
    output_bytes: Optional[bytes]


def _newest_xlsx_since(folder: Path, since_ts: float) -> Optional[Path]:
    """Newest .xlsx in *folder* modified at/after *since_ts* (handles the
    timestamped fallback the check writes when its default file is locked)."""
    if not folder.is_dir():
        return None
    candidates = [p for p in folder.glob("*.xlsx")
                  if not p.name.startswith("~$") and p.stat().st_mtime >= since_ts - 1]
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def run_checker(checker: Checker, items: list, *, aux_lists: Optional[dict] = None,
                extra_args: Optional[list] = None, env_overrides: Optional[dict] = None,
                on_line=None, timeout: Optional[int] = None) -> RunResult:
    """Run one check over *items* (Semantic Scholar records) and return its Excel.

    We write the uploaded records to a temp corpus dir, point ``BEALLS_CORPUS_DIR``
    at it, and run the check as a subprocess (it calls ``load_dotenv()`` itself,
    so LLM keys are picked up from the repo's ``.env``).

    *aux_lists* maps a CLI flag to a list written to a temp JSON and passed to the
    check — used for ``--whitelist`` / ``--blacklist``; empty lists are skipped.
    *on_line(line)*, if given, is called for each stdout line as it arrives (so
    the UI can show live progress); otherwise output is just captured.
    """
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="bealls_ui_") as tmp:
        tmp = Path(tmp)
        env = os.environ.copy()
        env.update(env_overrides or {})
        # The corpus dir is globbed for *.json, so it must contain ONLY the
        # uploaded corpus — keep it in its own subdir away from the aux files.
        corpus_dir = tmp / "corpus"
        corpus_dir.mkdir()
        (corpus_dir / "uploaded_corpus.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8")
        env["BEALLS_CORPUS_DIR"] = str(corpus_dir)

        # ``-u`` (unbuffered) so the child's print()s reach us line-by-line even
        # though stdout is a pipe, not a terminal — otherwise block buffering
        # hides all progress until the process exits.
        cmd = [sys.executable, "-u", str(checker.script)]
        for flag, entries in (aux_lists or {}).items():
            if not entries:
                continue
            aux_file = tmp / (flag.lstrip("-") + ".json")   # in tmp, never the corpus dir
            aux_file.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            cmd += [flag, str(aux_file)]
        cmd += list(extra_args or [])

        if on_line is None:
            proc = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT),
                                  capture_output=True, text=True, timeout=timeout)
            stdout, stderr, returncode = proc.stdout or "", proc.stderr or "", proc.returncode
        else:
            # Stream stdout (stderr merged in, so order is preserved and the pipe
            # can't deadlock) so the caller can show live progress.
            proc = subprocess.Popen(cmd, env=env, cwd=str(REPO_ROOT),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            captured = []
            for line in proc.stdout:
                line = line.rstrip("\n")
                captured.append(line)
                try:
                    on_line(line)
                except Exception:
                    pass                       # a UI hiccup must not kill the run
            proc.wait(timeout=timeout)
            stdout, stderr, returncode = "\n".join(captured), "", proc.returncode

    out_path = checker.output if checker.output.exists() else None
    fresh = _newest_xlsx_since(checker.output.parent, started)
    if fresh is not None:
        out_path = fresh

    output_bytes = out_path.read_bytes() if out_path else None
    return RunResult(
        ok=(returncode == 0 and output_bytes is not None),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output_path=out_path,
        output_bytes=output_bytes,
    )


def build_input_quality_xlsx(items: list, flagged: list) -> bytes:
    """Build a downloadable Excel of the input data-quality report (all papers).

    *flagged* is the list of ``(index, paper, warnings)`` from
    ``input_quality.validate_papers``; every paper gets a row, with "OK" when it
    has no warnings.
    """
    flags_by_idx = {i: warns for i, _paper, warns in flagged}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Input data quality"
    headers = ["#", "Title", "Venue", "DOI", "Data-quality warnings"]
    widths = [5, 55, 38, 26, 75]
    for col, (header, width) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        ws.column_dimensions[cell.column_letter].width = width
    for i, paper in enumerate(items, 1):
        pv = paper.get("publicationVenue") or {}
        warns = flags_by_idx.get(i, [])
        ws.cell(row=i + 1, column=1, value=i)
        ws.cell(row=i + 1, column=2, value=paper.get("title") or "")
        ws.cell(row=i + 1, column=3, value=paper.get("venue") or pv.get("name") or "")
        ws.cell(row=i + 1, column=4, value=(paper.get("externalIds") or {}).get("DOI") or "")
        wcell = ws.cell(row=i + 1, column=5, value="OK" if not warns else "\n".join(warns))
        wcell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@dataclass
class BatchCsvResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    csv_bytes: Optional[bytes]
    rows: list                       # [{"SS-ID", "DOI", "Status"}], input order


def _parse_status_csv(csv_bytes: bytes) -> list:
    """Parse the batch CSV into a list of ``{"SS-ID","DOI","Status"}`` dicts."""
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    return [{"SS-ID": r.get("SS-ID", ""), "DOI": r.get("DOI", ""),
             "Status": r.get("Status", "")} for r in reader]


def run_batch_csv(items: list, *, crossref: bool = True, aux_lists: Optional[dict] = None,
                  extra_args: Optional[list] = None,
                  on_line=None, timeout: Optional[int] = None) -> BatchCsvResult:
    """Run the batch check over *items* and return the SS-ID/DOI/Status CSV.

    Unlike :func:`run_checker` (which feeds a corpus dir and reads back an Excel),
    the batch script takes a single input file as a positional argument and writes
    a CSV — so this uses its own invocation.  Still a subprocess, to keep the
    module-name isolation the top-of-file docstring describes.

    *aux_lists* is the same mapping :func:`run_checker` takes, and is passed on for
    the same reason: this CSV is offered as an add-on to a run that may have had a
    whitelist or a blacklist, and one that ignored them would disagree with the
    workbook beside it about the same papers.  *extra_args* carries the reference-list
    selection for exactly the same reason — this function builds its command by hand,
    so without it a CSV would silently be screened against a different set of lists
    than the workbook it sits beside.
    """
    with tempfile.TemporaryDirectory(prefix="bealls_csv_") as tmp:
        tmp = Path(tmp)
        in_file = tmp / "input.json"
        in_file.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        out_file = tmp / "status.csv"
        cmd = [sys.executable, "-u", str(_BATCH_SCRIPT), str(in_file),
               "-o", str(out_file), "--crossref", "on" if crossref else "off"]
        for flag, entries in (aux_lists or {}).items():
            if not entries:
                continue
            aux_file = tmp / (flag.lstrip("-") + ".json")
            aux_file.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            cmd += [flag, str(aux_file)]
        cmd += list(extra_args or [])
        env = os.environ.copy()
        if on_line is None:
            proc = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT),
                                  capture_output=True, text=True, timeout=timeout)
            stdout, stderr, returncode = proc.stdout or "", proc.stderr or "", proc.returncode
        else:
            proc = subprocess.Popen(cmd, env=env, cwd=str(REPO_ROOT),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            captured = []
            for line in proc.stdout:
                line = line.rstrip("\n")
                captured.append(line)
                try:
                    on_line(line)
                except Exception:
                    pass
            proc.wait(timeout=timeout)
            stdout, stderr, returncode = "\n".join(captured), "", proc.returncode
        # Read inside the context — the temp dir (and the CSV) vanish on exit.
        csv_bytes = out_file.read_bytes() if out_file.exists() else None
    rows = _parse_status_csv(csv_bytes) if csv_bytes else []
    return BatchCsvResult(ok=(returncode == 0 and csv_bytes is not None),
                          returncode=returncode, stdout=stdout, stderr=stderr,
                          csv_bytes=csv_bytes, rows=rows)


def build_status_xlsx(rows: list) -> bytes:
    """Colour-coded Excel of the SS-ID/DOI/Status rows.

    The Status cell is filled with the canonical per-status colour
    (:data:`STATUS_FILL_COLORS`), so the download matches the main report and the
    UI preview.  A CSV can't carry colour; this xlsx is the human-friendly twin.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Status"
    for col, (header, width) in enumerate(zip(("SS-ID", "DOI", "Status"), (42, 28, 14)), 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        ws.column_dimensions[cell.column_letter].width = width
    for i, row in enumerate(rows, 2):
        ws.cell(row=i, column=1, value=row.get("SS-ID", ""))
        ws.cell(row=i, column=2, value=row.get("DOI", ""))
        status_cell = ws.cell(row=i, column=3, value=row.get("Status", ""))
        hex_color = STATUS_FILL_COLORS.get(row.get("Status", ""))
        if hex_color:
            status_cell.fill = PatternFill("solid", fgColor=hex_color)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# What makes a JSON object a paper record rather than something else shaped like
# one.  Any single field is enough — a record with only a DOI is thin but real,
# and this is a sanity check on the *file*, not a second data-quality pass.
_PAPER_FIELDS = ("paperId", "title", "publicationVenue", "venue", "externalIds", "repo")


def normalize_upload(parsed) -> list:
    """Coerce uploaded/pasted JSON into a list of paper objects.

    Accepts a list of objects, a single object, or the envelope Semantic
    Scholar's own API answers with.  Raises ValueError with a friendly message
    otherwise.
    """
    if isinstance(parsed, dict):
        # Semantic Scholar answers a search or a batch request with the records
        # wrapped: ``{"total": .., "offset": .., "data": [ ... ]}``.  Saving that
        # response to a file is the obvious way to get papers out of the API, and
        # it used to be read as *one* paper — an object with no title and no
        # venue — so the app said "Loaded 1 paper(s) — ready to run" and the
        # check came back with a single `no_venue` row.  Nothing anywhere said
        # the file had been misread, which is the worst way for this to fail.
        data = parsed.get("data")
        if isinstance(data, list):
            parsed = data
        elif any(k in parsed for k in _PAPER_FIELDS):
            return [parsed]                 # a single record, as before
        else:
            keys = ", ".join(f"`{k}`" for k in list(parsed)[:4]) or "nothing"
            raise ValueError(
                f"This is a JSON object, but not a paper record: it carries {keys} and "
                "none of the fields a Semantic Scholar record has (`title`, `paperId`, "
                "`publicationVenue`, `venue`, `externalIds`). If it came from the "
                "Semantic Scholar API, the papers are under its `data` key — upload that "
                "part, or the whole response, which is read the same way.")
    if isinstance(parsed, list):
        if not all(isinstance(x, dict) for x in parsed):
            raise ValueError("JSON list must contain paper objects (dicts).")
        return parsed
    raise ValueError("Upload must be a JSON list of papers or a single paper object.")
