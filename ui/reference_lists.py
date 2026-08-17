"""The reference lists a run can be checked against — what they are, and whether
this computer holds them.

Step 3 offers them as choices; the Settings page downloads them.  Both read this
module, so neither has to know how a list is stored or fetched, and adding a
third list is a registry entry in ``config.PROVIDERS`` plus its fetcher — nothing
in the UI names a particular list.

Import-safe (no Streamlit), like ``runners.py``, so it can be exercised on its own.
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BEALLS = REPO_ROOT / "src" / "bealls_list_check"
if str(_BEALLS) not in sys.path:
    sys.path.insert(0, str(_BEALLS))

from config import PROVIDERS, DEFAULT_PROVIDERS      # noqa: E402


def ids():
    """Every known list id, in registry order (Beall's first)."""
    return list(PROVIDERS)


def default_ids():
    """The lists a run uses when the user changes nothing."""
    return list(DEFAULT_PROVIDERS)


def label(pid):
    return PROVIDERS.get(pid, {}).get("label", pid)


def home(pid):
    return PROVIDERS.get(pid, {}).get("home", "")


def is_vendored(pid):
    """True when the list ships with the tool and needs no download.

    False means the repository does not redistribute it — because its publisher
    grants no licence to do so — and each user fetches their own copy.
    """
    return bool(PROVIDERS.get(pid, {}).get("vendored"))


def status(pid):
    """What this computer holds for one list.

    ``{"held", "entries", "scraped_utc", "age_days", "bytes", "counts", "error"}``.
    ``held`` is False when there is no local copy; for a vendored list that
    cannot normally happen, and means the snapshot is missing.
    """
    spec = PROVIDERS.get(pid) or {}
    path = spec.get("snapshot")
    out = {"held": False, "entries": 0, "scraped_utc": "", "age_days": None,
           "bytes": 0, "counts": {}, "error": ""}
    if not path or not Path(path).exists():
        return out
    try:
        out["bytes"] = Path(path).stat().st_size
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError) as exc:
        out["error"] = f"the local copy could not be read ({exc})"
        return out

    meta = snap.get("meta") or {}
    out["held"] = True
    out["entries"] = len(snap.get("entries") or [])
    out["counts"] = meta.get("counts") or {}
    out["scraped_utc"] = meta.get("scraped_utc") or ""
    if out["scraped_utc"]:
        try:
            when = datetime.fromisoformat(out["scraped_utc"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            out["age_days"] = (datetime.now(timezone.utc) - when).days
        except ValueError:
            pass
    return out


def usable(pid):
    """Can a run actually use this list right now?"""
    return is_vendored(pid) or status(pid)["held"]


def missing(pids):
    """Which of *pids* have no local copy — the ones a run would fail on."""
    return [p for p in pids if not usable(p)]


def fetch_command(pid):
    """The command that fetches one list, or None if it needs no fetching."""
    script = (PROVIDERS.get(pid) or {}).get("fetch_with")
    if not script:
        return None
    return [sys.executable, "-u", str(_BEALLS / script)]


def fetch(pid, *, full=False, on_line=None, timeout=900):
    """Fetch or update one list's local copy.

    Runs the list's own fetcher as a subprocess — the same isolation
    ``runners.py`` uses, and for the same reason: these scripts import flat
    module names that would collide in this process.  Returns
    ``(ok, output_text)``; *on_line* receives each line as it arrives so the page
    can show progress.
    """
    cmd = fetch_command(pid)
    if cmd is None:
        return False, f"{label(pid)} ships with the tool and needs no download."
    if full:
        cmd.append("--full")
    started = time.time()
    lines = []
    try:
        proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:
                    pass                      # a UI hiccup must not kill the fetch
        proc.wait(timeout=timeout)
        ok = proc.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        lines.append(str(exc))
        return False, "\n".join(lines).strip()
    lines.append(f"({time.time() - started:.0f}s)")
    return ok, "\n".join(lines)


def cli_args(selected):
    """The ``--list`` arguments for a selection, or ``[]`` when it is the default.

    Omitting the flag entirely when the selection is the default keeps an
    untouched Step 3 producing exactly the command line it produced before this
    control existed.
    """
    chosen = [p for p in ids() if p in set(selected or ())]
    if not chosen or chosen == default_ids():
        return []
    args = []
    for pid in chosen:
        args += ["--list", pid]
    return args
