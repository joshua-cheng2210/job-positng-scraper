"""Deterministic filtering. Runs before any LLM sees a posting.

Cuts a few thousand raw postings to under a hundred for free. Every rule here
is a pure function over a Posting so it can be unit tested without a network.
"""
from __future__ import annotations

import re
from typing import Iterable

from .models import Posting

# --- 1. hard excludes: not remotely a tech role -------------------------------

EXCLUDE_TITLE = [
    r"\bfaculty\b", r"\btenure[- ]track\b", r"\bprofessor\b", r"\blecturer\b",
    r"\badjunct\b", r"\binstructor\b", r"\bdean\b", r"\bprovost\b",
    r"\bnurs(e|ing)\b", r"\bphysician\b", r"\bclinician\b", r"\btherapist\b",
    r"\bsonographer\b", r"\bradiolog", r"\bpharmac", r"\bdental\b", r"\bveterinar",
    r"\bcoach\b", r"\bathletic", r"\bcustodian\b", r"\bcustodial\b",
    r"\bgroundskeeper\b", r"\blandscap", r"\bplumber\b", r"\belectrician\b",
    r"\bcarpenter\b", r"\bwelder\b", r"\bmechanic\b", r"\bhvac\b", r"\bmason\b",
    r"\bpainter\b", r"\blocksmith\b", r"\bboiler\b", r"\bmaintenance worker\b",
    r"\btruck driv", r"\bbus driver\b", r"\bsnow plow\b", r"\bfood service\b",
    r"\bcook\b", r"\bchef\b", r"\bbarista\b", r"\bcashier\b", r"\bdining\b",
    r"\bhousekeep", r"\bpolice\b", r"\bsecurity officer\b", r"\bfirefighter\b",
    r"\bparking\b", r"\bmail (carrier|clerk)\b", r"\bwarehouse\b",
    r"\bdevelopment officer\b", r"\bfundrais", r"\badvancement\b", r"\bgift officer\b",
    r"\badmissions counselor\b", r"\bacademic advis", r"\bcareer coach\b",
    r"\bsocial work", r"\bcounselor\b", r"\bchaplain\b", r"\blibrarian\b",
    # postdoc requires a completed PhD -- categorical mismatch for a new grad,
    # matches "post-doc", "postdoc", and "postdoctoral" (any suffix after "doc").
    r"\bpost[- ]?doc",
]

# --- 2. seniority: he is a May 2026 new grad ---------------------------------

EXCLUDE_SENIORITY = [
    r"\bsenior\b", r"\bsr\.?\b", r"\bstaff (engineer|scientist)\b",
    r"\blead\b", r"\bprincipal\b", r"\bmanager\b", r"\bdirector\b",
    r"\bhead of\b", r"\bchief\b", r"\bsupervisor\b", r"\bexecutive\b",
    r"\bvice president\b", r"\bassociate dean\b", r"\barchitect\b",
    r"\bIII\b", r"\bIV\b", r"\bV\b",
]

# --- 3. includes: what a university actually calls his job -------------------

INCLUDE_TITLE = [
    r"\bsoftware (engineer|developer)\b", r"\bresearch software\b",
    r"\bapplication (developer|programmer|analyst)\b", r"\bweb developer\b",
    r"\bfull[- ]?stack\b", r"\bfront[- ]?end\b", r"\bback[- ]?end\b",
    r"\bprogrammer\b", r"\bscientific programmer\b", r"\bdeveloper\b",
    r"\bdata (analyst|scientist|engineer)\b", r"\bresearch data\b",
    r"\bbusiness intelligence\b", r"\bbi developer\b", r"\banalytics\b",
    r"\bstatistic(al|ian)\b", r"\bbioinformatic", r"\bcomputational\b",
    r"\bsystems (analyst|administrator|engineer)\b", r"\bsysadmin\b",
    r"\bdatabase (administrator|analyst|developer)\b", r"\bdba\b",
    r"\bdevops\b", r"\bcloud\b", r"\bplatform engineer\b", r"\bsite reliability\b",
    r"\binformation technology\b", r"\bit (specialist|analyst|support)\b",
    r"\bresearch (professional|associate|assistant|technician|specialist)\b",
    r"\bresearch scientist\b", r"\bmachine learning\b",
    r"\bartificial intelligence\b", r"\b(ai|ml) engineer\b",
    r"\bgis\b", r"\bqa\b", r"\bquality assurance\b", r"\btest engineer\b",
    r"\binstructional (technolog|design)", r"\bdigital scholarship\b",
    r"\binstitutional research\b", r"\bhpc\b", r"\bhigh[- ]performance comput",
]

# --- 4. structural blockers: sink the application regardless of skill --------

HARD_BLOCKERS = {
    "US citizenship required": [
        r"must be a u\.?s\.? citizen", r"u\.?s\.? citizenship (is )?required",
        r"restricted to u\.?s\.? citizens", r"citizenship requirement",
    ],
    "Security clearance required": [
        r"security clearance", r"secret clearance", r"\btop secret\b",
        r"\bts/sci\b", r"ability to obtain (and maintain )?a clearance",
    ],
    "Export control restricted": [
        r"export control", r"\bitar\b", r"\bear\b restricted",
    ],
}

# --- 5. sponsorship signals --------------------------------------------------
# Deliberately NOT used to reject. E-Verify enrolment and H-1B cap-exemption are
# unrelated: a university that declines E-Verify can still hire on the initial
# 12-month OPT and file a cap-exempt H-1B with no lottery. A "no STEM OPT" line
# is a data point for prioritisation, not a stop sign.

SPONSORSHIP_PATTERNS: list[tuple[str, list[str]]] = [
    ("no_sponsorship_any", [
        r"not (be )?(able|eligible) to sponsor",
        r"(will|does) not sponsor (any )?(work )?(visa|immigration)",
        r"no visa sponsorship",
        r"unable to sponsor.*(now|future)",
    ]),
    ("no_stem_opt", [
        r"not (enrolled|participate) in e-?verify",
        r"does not participate in e-?verify",
        r"not (able|eligible) to support.*stem opt",
        r"cannot support.*stem opt",
        r"stem opt.*not (available|supported)",
    ]),
    ("h1b_possible", [
        r"may be able to sponsor",
        r"(will|can) sponsor.*h-?1b",
        r"h-?1b.*sponsorship (is )?available",
        r"visa sponsorship (is )?available",
        r"cap[- ]exempt",
    ]),
]


def _any(patterns: Iterable[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


def is_excluded(p: Posting) -> str | None:
    """Return the reason this posting is dropped, or None to keep it."""
    title = p.title.lower()
    hit = _any(EXCLUDE_TITLE, title)
    if hit:
        return f"non-tech role ({hit})"
    hit = _any(EXCLUDE_SENIORITY, p.title)   # case-sensitive for III/IV/V
    if hit:
        return f"too senior ({hit.strip()})"
    if not _any(INCLUDE_TITLE, title):
        return "title does not match any tech pattern"
    return None


def annotate(p: Posting) -> Posting:
    """Attach sponsorship flag and hard blockers. Never drops a posting."""
    text = p.haystack

    p.hard_blockers = [
        label for label, pats in HARD_BLOCKERS.items() if _any(pats, text)
    ]

    for flag, pats in SPONSORSHIP_PATTERNS:
        hit = _any(pats, text)
        if hit:
            p.sponsorship_flag = flag
            p.sponsorship_evidence = hit
            break
    else:
        p.sponsorship_flag = "unknown"

    return p


def apply_filters(postings: list[Posting]) -> tuple[list[Posting], dict[str, int]]:
    kept: list[Posting] = []
    stats: dict[str, int] = {}
    seen: set[str] = set()

    for p in postings:
        if p.key in seen:
            stats["duplicate"] = stats.get("duplicate", 0) + 1
            continue
        seen.add(p.key)

        reason = is_excluded(p)
        if reason:
            bucket = reason.split(" (")[0]
            stats[bucket] = stats.get(bucket, 0) + 1
            continue
        kept.append(annotate(p))

    stats["kept"] = len(kept)
    return kept, stats
