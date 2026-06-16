"""Adapter layer between the Streamlit UI and the Beall's List check scripts.

Each check is a standalone script that reads its corpus from a folder pointed at
by ``BEALLS_CORPUS_DIR`` and writes an Excel file to a fixed location.  Rather
than import the scripts in-process (they reuse module names like
``config``/``match`` that would collide), we run each as a **subprocess** with
the uploaded corpus injected via an environment variable, then read back the
Excel it produced.

This module is import-safe (no Streamlit) so it can be unit-tested on its own.
"""

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
from openpyxl.styles import Alignment, Font

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
RC = SRC                          # the dir that holds the ``common`` package (app.py adds it to sys.path)
_BEALLS = SRC / "bealls_list_check"


@dataclass(frozen=True)
class Checker:
    id: str
    label: str
    script: Path            # script to run as a subprocess
    output: Path            # Excel it writes (we also scan its folder for fresher files)
    needs_llm: bool = False
    note: str = ""
    description: str = ""        # plain-language "what this check answers", shown in the UI


CHECKERS: List[Checker] = [
    Checker("bealls.deterministic", "Fast check — rules only (no LLM)",
            _BEALLS / "bealls_list_check.py",
            _BEALLS / "results" / "bealls_list_results.xlsx",
            description="Is the paper's journal/publisher on Beall's List of potentially "
                        "predatory venues? Fast, deterministic, no tokens."),
    Checker("bealls.llm", "Thorough check — rules + LLM over the review papers",
            _BEALLS / "bealls_llm_check.py",
            _BEALLS / "results" / "bealls_llm_results.xlsx",
            needs_llm=True,
            description="Same as the fast check, then an LLM gives a keep/exclude/uncertain "
                        "recommendation for each 'review' paper. Uses tokens."),
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


def normalize_upload(parsed) -> list:
    """Coerce uploaded/pasted JSON into a list of paper objects.

    Accepts a list of objects or a single object (wrapped). Raises ValueError
    with a friendly message otherwise.
    """
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        if not all(isinstance(x, dict) for x in parsed):
            raise ValueError("JSON list must contain paper objects (dicts).")
        return parsed
    raise ValueError("Upload must be a JSON list of papers or a single paper object.")
