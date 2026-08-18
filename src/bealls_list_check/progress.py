"""Live progress reporting for the long passes — the corpus and the workbook.

A check spends its time in two places, and until this module existed it went
silent in both.  The corpus pass prints one line before it starts and one after
it ends, which on an 18k-paper corpus with Crossref on is 38 minutes apart; the
workbook writer prints nothing at all while openpyxl lays out a block of rows
per paper and then serialises the file.  The work was fine in both cases — only
the reporting was missing, so the UI sat on a stale caption and the run looked
hung.

:class:`Progress` fixes that with one line, emitted on a timer:

    [11426/18482] 62% | 8 papers/s | ~14m left | 11400 Crossref lookups

The bracketed counter is the machine-readable part — ``ui/app.py`` parses
exactly that to drive its progress bar, and takes the rest of the line as the
bar's label — so anything that ticks here shows up in the UI without the UI
needing to know which check is running or what it counts.

It lives in its own module rather than beside either caller because both need
it: ``bealls_list_check`` imports the workbook writer from ``report``, so a
ticker defined in the conductor could not be imported back into the writer.
"""

import sys
import time


def human_duration(seconds):
    """A rough duration a person can read at a glance: ``45s``, ``12m``, ``1h 05m``."""
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


class Progress:
    """A ticker for a long pass, printing ``[done/total]`` plus a plain detail.

    Lines are emitted on a TIMER (*every* seconds), not every Nth item, which is
    what keeps both ends honest: a slow pass is visibly alive however slow each
    item is, and a fast one does not flood the pipe with thousands of lines the
    UI would have to re-render.  *note* is a zero-argument callable so the detail
    it builds costs nothing on the ticks that are not emitted.

    Everything printed is ASCII, like the rest of this program's output: stdout
    is a pipe to the UI (or a Windows console) whose encoding is the machine's
    locale rather than UTF-8, and a decorative separator is not worth risking an
    encoding error raised from inside a loop.
    """

    def __init__(self, label="papers", *, every=1.0, note=None, stream=None):
        self.label = label
        self.every = every
        self.note = note
        self.stream = stream if stream is not None else sys.stdout
        self.total = 0
        self.done = 0
        self.started = time.time()
        # Start the clock now rather than at 0, so the first line comes one
        # interval in instead of on the very first item: a rate worked out from a
        # single sample is wrong by orders of magnitude, and a bar that opens by
        # claiming "~32m left" on a pass that takes four seconds is worse than
        # one that says nothing for a second.
        self._last = self.started
        self._emitted = False
        self._last_done = -1                # the count the most recent line reported

    def add_total(self, n):
        """Grow (or shrink, with a negative *n*) the denominator.

        Called as each corpus file is parsed, and again when a duplicate record
        is skipped — a denominator counting records the pass will never reach
        would leave the bar stuck short of the end.
        """
        self.total = max(self.total + n, 0)

    def tick(self, n=1):
        """Count *n* more items, printing a line if one is due.

        Reaching the total also emits, so a bar that has been moving always lands
        on 100% rather than stopping at whatever the last timer tick caught — but
        only if this pass ever reported at all.  A pass that finished inside a
        single interval needed no progress and says nothing, which keeps a small
        corpus (or the test fixtures) from printing a bar it had no use for.
        """
        self.done += n
        now = time.time()
        due = (now - self._last) >= self.every
        finished = self._emitted and self.total and self.done >= self.total
        if not (due or finished):
            return
        self._last = now
        self._emit()

    def heartbeat(self):
        """Re-print the current line without advancing the count.

        For a stretch that is slow but finishes no items — the concurrent
        Crossref prefetch, which spends half a minute filling the cache before a
        single paper can be completed.  The counter stands still (nothing
        finished, and claiming otherwise would be a lie) while the rate, the
        estimate and the note keep moving, so the lookups can be watched climbing
        and the reader can tell waiting from hanging.
        """
        now = time.time()
        if (now - self._last) < self.every:
            return
        self._last = now
        self._emit()

    def close(self):
        """Print a final line, so the last thing shown is the finished count.

        A no-op when the pass was short enough that nothing ever ticked (no point
        opening and closing a bar for a corpus of five), or when the last tick
        already reported this count — reaching the total emits a line of its own,
        and closing on top of that printed the finished line twice.
        """
        if self._emitted and self.done != self._last_done:
            self._emit()

    def _emit(self):
        self._emitted = True
        self._last_done = self.done
        elapsed = max(time.time() - self.started, 1e-6)
        rate = self.done / elapsed
        bits = []
        if self.total:
            bits.append(f"{100 * self.done / self.total:.0f}%")
        bits.append(f"{rate:,.0f} {self.label}/s")
        if self.total and rate > 0 and self.done < self.total:
            bits.append(f"~{human_duration((self.total - self.done) / rate)} left")
        note = self.note() if self.note else ""
        if note:
            bits.append(note)
        print(f"  [{self.done}/{self.total}] " + " | ".join(bits),
              file=self.stream, flush=True)


def crossref_note(mode):
    """A ``note`` for :class:`Progress` reporting the Crossref spend, or None.

    On a big corpus the DOI lookups ARE the run: one network round-trip per paper
    under ``--crossref all``, which is why an 18k-paper corpus takes ~38 minutes
    where the offline work takes ~30 seconds.  A progress line that did not count
    them would hide the one number explaining the wait.  Returns None when
    Crossref is off, so an offline run says nothing about it.
    """
    if mode == "off":
        return None
    import data_quality                      # local: only needed when Crossref is on
    return lambda: f"{data_quality.lookups_made()} Crossref lookups"
