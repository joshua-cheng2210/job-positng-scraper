"""Resume-match scoring.

Cheap, deterministic, explainable. Its only job is to order the survivors so an
LLM pass (/job-fit) runs on the top N instead of all of them.

Known bias, stated plainly: keyword overlap rewards jobs matching who Josh
already is. `STRETCH_SIGNALS` deliberately adds points for high-value roles he
is NOT yet a match for, so the ranking surfaces some reach targets rather than
converging on the safest possible list.
"""
from __future__ import annotations

import re
from datetime import date

from .models import Posting

# Josh's skill profile. Used to be profile.yaml; inlined here so scoring has
# no config file to go stale or get edited out of sync with score.py. Edit
# this dict directly -- no re-collection needed, scoring re-runs instantly.
# Category weights live in KEYWORD_CATEGORIES below.
PROFILE: dict = {
    "languages": [           # +1.5 each -- strongest signal, weighted above tooling
        "Python", "Java", "JavaScript", "C++", "SQL", "HTML", "CSS", "OCaml", "R",
        # "REALBASIC" / "XOJO" deliberately omitted -- no university posting is
        # going to ask for it, and it just adds noise to the regex pass.
    ],
    "frameworks": [          # +1.0 each
        "Node.js", "React", "Angular", "Flask", "Vite", "Tailwind CSS",
        "Bootstrap", "LangChain", "Hugging Face", "Django",
    ],
    "databases_tools": [     # +1.0 each
        "PostgreSQL", "MySQL", "Git", "Docker", "Jira", "Tableau", "Excel",
    ],
    "cloud_deployment": [    # +1.0 each
        "AWS", "S3", "CloudFront", "Lambda", "GitHub Pages", "Render",
    ],
    "skills": [               # +1.0 each -- legacy bucket for anything not categorized above
        "REST API", "Linux",
    ],
    "data_analytics": [      # +0.75 each
        "NumPy", "pandas", "Matplotlib", "Seaborn", "OpenCV", "Regex",
        "ChromaDB", "Data Cleaning",
    ],
    "design_productivity": [ # +0.5 each
        "Figma", "JMP", "TeamDynamix", "TargetProcess",
    ],
    "nice_to_have": [         # +0.5 each -- adjacent, or things he can learn in a weekend
        "TypeScript", "Power BI", "MATLAB", "Kubernetes", "Azure", "GCP",
        "Spark", "Airflow", "scikit-learn", "PyTorch", "TensorFlow",
        "Jenkins", "Agile",
    ],
    "graduation": "2026-05",
}


TITLE_TIERS = {
    3.0: [r"\bsoftware (engineer|developer)\b", r"\bresearch software\b",
          r"\bdata (analyst|scientist|engineer)\b", r"\bapplication developer\b"],
    2.0: [r"\bprogrammer\b", r"\bweb developer\b", r"\bbusiness intelligence\b",
          r"\bsystems analyst\b", r"\bcomputational\b", r"\bresearch professional\b"],
    1.0: [r"\bit (specialist|analyst)\b", r"\bdatabase\b", r"\bqa\b",
          r"\bresearch (associate|assistant|technician)\b"],
}

STRETCH_SIGNALS = [
    (r"\bmachine learning\b", 2.0),
    (r"\bartificial intelligence\b", 2.0),
    (r"\bhpc\b|\bhigh[- ]performance comput", 2.0),
    (r"\bbioinformatic", 1.5),
    (r"\bresearch software engineer\b", 2.5),
]

ENTRY_SIGNALS = [
    (r"\bentry[- ]level\b", 2.0),
    (r"\bnew grad", 2.5),
    (r"\b(0|1)[-–](1|2|3) years", 1.5),
    (r"\bbachelor'?s? degree\b", 1.0),
    (r"\b(I|1)\b(?!\w)", 0.5),          # "Analyst I", "Developer 1"
]

EXPERIENCE_PENALTY = re.compile(r"\b(\d+)\+?\s*(?:-|to|–)?\s*\d*\s*years?\b", re.I)

# PROFILE keys scored here, in priority order, with their per-match weight.
# Languages sit highest on purpose -- language fluency is the strongest signal
# of "can actually do this job" and Josh asked to weight them above tooling.
# `skills` / `nice_to_have` are legacy catch-all buckets kept for anything that
# doesn't fit the more specific categories below.
KEYWORD_CATEGORIES: dict[str, float] = {
    "languages": 1.5,
    "frameworks": 1.0,
    "databases_tools": 1.0,
    "cloud_deployment": 1.0,
    "skills": 1.0,
    "data_analytics": 0.75,
    "nice_to_have": 0.5,
    "design_productivity": 0.5,
}


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Word-boundary match that also works for keywords ending/starting in a
    non-word character (C++, ES6+, .NET, etc.), where \\b silently fails to
    match because \\b requires a transition into a \\w character."""
    escaped = re.escape(keyword.lower())
    return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])")


RECENCY_BONUS = 3.0    # posted_date == today (all we have is day granularity,
                       # so "today" is the closest available proxy for "<24h ago")
RECENCY_SOON_BONUS = 2.0   # posted 1-2 days ago ("<3 days ago")

# He's job hunting for a full-time role -- a part-time or temporary posting
# isn't disqualifying (some are still worth applying to, e.g. a temp role
# that regularly converts to full-time), but it's a real negative signal, so
# it's a scoring penalty here rather than a filters.py hard exclude.
EMPLOYMENT_TYPE_PENALTIES = [
    ("part-time", r"\bpart[- ]time\b", 2.0),
    ("temporary", r"\btemporary\b", 2.0),
]

# Fairness offset for unscraped postings. haystack = title + department +
# description, so with description missing every keyword-category match,
# stretch/entry signal, years-of-experience check, and sponsorship/blocker
# scan (all of which run on haystack) finds nothing -- not just no bonus,
# also no penalty. An unscraped posting is squeezed into a narrow band near
# its title-tier score alone, while scraped postings spread out both above
# and below it. Without this offset, a genuinely great-fit posting that
# simply hasn't been enriched yet (see run.py's enrich_survivors -- it's
# bounded to survivors, not guaranteed to succeed for all of them) can rank
# below a mediocre scraped posting purely for lack of text -- a real missed
# opportunity, not a reflection of actual fit. This doesn't claim the
# posting IS a good match; description_scraped stays 0 and the workbook
# still flags it as unverified -- it just stops the ranking from silently
# penalizing "we don't know yet" the same as "we checked, and it's average."
UNSCRAPED_BENEFIT_OF_DOUBT = 3.0


def score(p: Posting, profile: dict = PROFILE, today: date | None = None) -> Posting:
    today = today or date.today()
    pts = 0.0
    why: list[str] = []

    title = p.title.lower()
    for weight, pats in TITLE_TIERS.items():
        for pat in pats:
            if re.search(pat, title):
                pts += weight
                why.append(f"title match +{weight}")
                break
        else:
            continue
        break

    text = p.haystack

    for category, weight in KEYWORD_CATEGORIES.items():
        for keyword in profile.get(category, []):
            if _keyword_pattern(keyword).search(text):
                pts += weight
                why.append(f"{keyword} ({category}) +{weight}")

    for pat, w in STRETCH_SIGNALS:
        if re.search(pat, text):
            pts += w
            why.append(f"stretch signal +{w}")

    for pat, w in ENTRY_SIGNALS:
        if re.search(pat, text):
            pts += w
            why.append(f"entry-level signal +{w}")

    # Recency bonus: freshly-posted roles are worth applying to first, before
    # the applicant pool grows. posted_date only has day granularity (no
    # timestamp from any adapter), so "posted today" stands in for "<24h ago"
    # and "posted 1-2 days ago" stands in for "<3 days ago". Mutually
    # exclusive -- only the stronger bonus applies.
    if p.posted_date:
        days_since = (today - p.posted_date).days
        if 0 <= days_since < 1:
            pts += RECENCY_BONUS
            why.append(f"posted <24h ago +{RECENCY_BONUS}")
        elif 1 <= days_since < 3:
            pts += RECENCY_SOON_BONUS
            why.append(f"posted {days_since}d ago +{RECENCY_SOON_BONUS}")

    # Part-time / temporary penalty: checked against the whole haystack (title
    # + department + description), not just the title, since some portals only
    # mention employment type in body text ("This is a temporary, grant-funded
    # position...").
    for label, pat, w in EMPLOYMENT_TYPE_PENALTIES:
        if re.search(pat, text):
            pts -= w
            why.append(f"{label} -{w}")

    # years-of-experience penalty: he graduates May 2026
    years = [int(m.group(1)) for m in EXPERIENCE_PENALTY.finditer(text)]
    if years:
        worst = max(years)
        if worst >= 5:
            pts -= 10.0
            why.append(f"{worst}+ yrs required -10.0")
        elif worst >= 3:
            pts -= 2.0
            why.append(f"{worst}+ yrs required -2.0")

    if p.hard_blockers:
        pts -= 10.0
        why.append("hard blocker -10.0")

    if p.sponsorship_flag == "no_sponsorship_any":
        pts -= 3.0
        why.append("no sponsorship of any kind -3.0")
    elif p.sponsorship_flag == "h1b_possible":
        pts += 2.0
        why.append("positive sponsorship language +2.0")
    elif p.sponsorship_flag == "no_stem_opt":
        pts -= 2.0
        why.append("no STEM OPT (cap-exempt H-1B still fine, but a minor negative "
                    "signal) -2.0")
    # no_stem_opt is still not an EXCLUDE -- cap-exempt H-1B needs no E-Verify/
    # STEM OPT, so Josh still applies. It's now a small scoring penalty rather
    # than neutral, since a plain STEM-OPT posting is a slightly safer bet than
    # one that explicitly rules it out. See the note in filters.py.

    if not p.description_scraped:
        pts += UNSCRAPED_BENEFIT_OF_DOUBT
        why.append(f"unscraped, benefit of the doubt (verify manually) "
                   f"+{UNSCRAPED_BENEFIT_OF_DOUBT}")

    p.score = round(pts, 2)
    p.score_reasons = why
    return p


def rank(postings: list[Posting], profile: dict = PROFILE,
         today: date | None = None) -> list[Posting]:
    return sorted(
        (score(p, profile, today) for p in postings),
        key=lambda x: x.score,
        reverse=True,
    )
