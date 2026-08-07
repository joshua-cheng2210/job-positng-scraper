"""Core data model. Every adapter returns List[Posting] and nothing downstream
knows which platform a posting came from."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#39;|&rsquo;", "'", text)
    text = re.sub(r"&quot;|&ldquo;|&rdquo;", '"', text)
    text = re.sub(r"\s+", " ", text)
    return text.strip() or None


@dataclass
class Posting:
    institution: str
    job_id: str
    title: str
    url: str
    platform: str                      # workday | peopleadmin | pageup | custom
    department: str | None = None
    location: str | None = None
    posted_date: date | None = None
    close_date: date | None = None
    description: str | None = None     # full text, used for sponsorship scanning
    description_scraped: int = 0       # 1 = verified-complete detail fetch (Workday
                                        # jobPostingInfo, or PeopleAdmin HTML page
                                        # parse). 0 = absent, or only the Atom feed's
                                        # partial <content> excerpt -- confirmed to
                                        # omit Minimum/Preferred Qualifications on
                                        # UNL/Utah/NC State postings. Don't trust
                                        # years/skills scoring on a 0 row.
    system: str | None = None          # e.g. "Minnesota State", "Big Ten"
    state: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # --- derived, filled in by the pipeline ---
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
    sponsorship_flag: str = "unknown"  # hard_stop | h1b_only | positive | unknown
    sponsorship_evidence: str | None = None
    hard_blockers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.title = _clean(self.title) or "(untitled)"
        self.department = _clean(self.department)
        self.location = _clean(self.location)
        self.description = _clean(self.description)

    @property
    def key(self) -> str:
        """Stable dedup key. Same posting seen twice (e.g. a school that appears
        in two targets) collapses to one row."""
        basis = f"{self.institution.lower()}|{self.job_id.lower()}"
        return hashlib.sha1(basis.encode()).hexdigest()[:16]

    @property
    def haystack(self) -> str:
        return " ".join(
            p for p in (self.title, self.department, self.description) if p
        ).lower()

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["score_reasons"] = "; ".join(self.score_reasons)
        d["hard_blockers"] = "; ".join(self.hard_blockers)
        for k in ("posted_date", "close_date"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d
