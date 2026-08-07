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


def score(p: Posting, profile: dict = PROFILE) -> Posting:
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

    # years-of-experience penalty: he graduates May 2026
    years = [int(m.group(1)) for m in EXPERIENCE_PENALTY.finditer(text)]
    if years:
        worst = max(years)
        if worst >= 5:
            pts -= 4.0
            why.append(f"{worst}+ yrs required -4.0")
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
    # no_stem_opt is intentionally NOT penalised -- cap-exempt H-1B needs no
    # E-Verify. See the note in filters.py.

    p.score = round(pts, 2)
    p.score_reasons = why
    return p


def rank(postings: list[Posting], profile: dict = PROFILE) -> list[Posting]:
    return sorted(
        (score(p, profile) for p in postings),
        key=lambda x: x.score,
        reverse=True,
    )
