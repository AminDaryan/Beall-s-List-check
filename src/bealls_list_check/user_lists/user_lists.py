"""User-supplied whitelist / blacklist for the Beall's check.

Two optional inputs, both JSON lists of ``{"name": ..., "domain": ...}`` (a
plain string is treated as a name; ``url`` is accepted in place of ``domain``):

  * **blacklist** — venues the user asserts are predatory.  They are turned into
    snapshot entries (``list_source="blacklist"``) and added to the BeallIndex,
    so a paper in one of them is flagged exactly like a real Beall entry.

  * **whitelist** — a *corpus selector*.  When non-empty, only papers whose
    venue matches a whitelist entry are evaluated; every other paper is dropped
    before the run and appears nowhere in the report, so all of it — the rows,
    the counts, the flagged sheet — is about the whitelisted venues alone.  An
    empty/absent whitelist means "check everything" (the default behaviour).

A paper is *in scope* if any one of three signals matches, tried most precise
first:

  * one of its venue ISSNs is whitelisted — an ISSN names one journal and
    nothing else, so this is the only signal that cannot widen the scope beyond
    the journal the user actually named;
  * any of its venue domains is the same as, or a subdomain of, a whitelisted
    domain (so whitelisting a publisher domain covers all its journals — useful
    deliberately, over-broad by accident, which is why the web app asks before
    adding a domain it recognises as a shared platform);
  * a whitelisted name appears as a whole-word phrase in one of the paper's
    venue names (so whitelisting the publisher "IEEE" matches "IEEE Transactions
    on …", without "nature" spuriously matching "signature").
"""

import json
import sys
from pathlib import Path

# This module now lives in <pkg>/user_lists/; put the package root and every foldered
# module (foo/foo.py) on the path so the flat 'from x import y' imports still work.
_ROOT = Path(__file__).resolve().parent.parent
for _p in [_ROOT, *(d for d in _ROOT.iterdir() if d.is_dir() and (d / f"{d.name}.py").exists())]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from normalize import normalize_name, normalize_host, normalize_issn
from match import venue_signals


def load_user_entries(path, list_source):
    """Load a user JSON list into snapshot-entry dicts (``list_source`` tagged).

    Skips entries carrying none of the three identifying signals — name, domain,
    ISSN.  An ISSN alone is enough (both lists match on it), so a whitelist built
    from a journal directory needs nothing but the identifier that directory is
    surest about.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON list (or single object) of venues.")

    entries = []
    for item in data:
        if isinstance(item, str):
            item = {"name": item}
        name = (item.get("name") or "").strip()
        url = (item.get("url") or item.get("domain") or "").strip()
        domain = normalize_host(url) if url else ""
        normalized_name = normalize_name(name)
        issns = [normalize_issn(i) for i in (item.get("issns") or []) if normalize_issn(i)]
        if not (domain or normalized_name or issns):
            continue
        entries.append({
            # Something to call the entry in a report or a log line, preferring
            # the most human of whatever it was given.
            "name": name or domain or issns[0],
            "url": item.get("url") or (f"https://{domain}" if domain else ""),
            "domain": domain,
            "normalized_name": normalized_name,
            "issns": issns,
            "list_source": list_source,
        })
    return entries


class WhitelistMatcher:
    """Decides whether a paper's venue is in the user's whitelist scope."""

    def __init__(self, entries):
        # Each entry contributes **one** identifier: the most precise it carries.
        # See ``match.user_entry_signal`` for why, and for what it costs. The
        # short version: choosing a journal out of a directory attaches its
        # official title *and* its ISSN, and treating the title as a second claim
        # widens the entry to every venue whose name contains that phrase — for
        # "Neural Networks", from one journal to eleven venues.
        from match import user_entry_signal
        self.domains, self.name_phrases, self.issns = set(), set(), set()
        for e in entries:
            signal = user_entry_signal(e)
            if signal == "issn":
                self.issns.update(e["issns"])
            elif signal == "domain":
                self.domains.add(e["domain"])
            elif signal == "name":
                # Space-padded, so the test is whole-word phrase containment.
                self.name_phrases.add(f" {e['normalized_name']} ")

    @property
    def active(self):
        return bool(self.domains or self.name_phrases or self.issns)

    def _domain_in_scope(self, host):
        if not host:
            return False
        labels = host.split(".")
        return any(".".join(labels[i:]) in self.domains for i in range(len(labels) - 1))

    def in_scope(self, paper):
        """True if the paper's venue belongs to a whitelisted publisher/journal.

        Signals are tried most precise first.  Unlike the matcher's tiers, which
        one hit is irrelevant here — the answer is a yes/no about scope, not a
        verdict that has to explain itself — so the order buys nothing but a
        cheap set lookup before the phrase scan.
        """
        domains, names, issns = venue_signals(paper)
        if any(i in self.issns for i in issns):
            return True
        if any(self._domain_in_scope(d) for d in domains):
            return True
        padded = [f" {n} " for n in names]
        return any(phrase in pn for phrase in self.name_phrases for pn in padded)
