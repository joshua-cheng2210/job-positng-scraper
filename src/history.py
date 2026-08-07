"""Cumulative posting history.

Each run writes a NEW workbook, but no posting is ever lost: `data/history.json`
accumulates every posting ever collected, keyed by institution|job_id, so a
posting that has since been taken down still appears in the History tab marked
`closed`.

Deduplication is by that key, not by row equality -- a posting whose title or
close date changed is the SAME posting, updated in place, not a second row.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .models import Posting

log = logging.getLogger(__name__)

CARRY_FIELDS = (
    "institution", "job_id", "title", "url", "platform", "department",
    "location", "posted_date", "close_date", "system", "state",
    "sponsorship_flag", "sponsorship_evidence", "hard_blockers", "score",
)


def _key(row: dict[str, Any]) -> str:
    return f"{str(row.get('institution', '')).lower()}|{str(row.get('job_id', '')).lower()}"


def load(path: str | Path) -> dict[str, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("%s is corrupt; starting a fresh history", path)
        return {}
    if isinstance(doc, list):                       # tolerate an older flat format
        return {_key(r): r for r in doc if isinstance(r, dict)}
    return doc if isinstance(doc, dict) else {}


def save(history: dict[str, dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=1, default=str), encoding="utf-8")


def update(history: dict[str, dict], current: Iterable[Posting],
           run_date: date | None = None) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Fold this run's postings into the history.

    Returns (history, changes) where changes has 'new' and 'closed' lists.
    Nothing is deleted -- a posting missing from this run is marked closed and
    keeps its last known values.
    """
    run_date = (run_date or date.today()).isoformat()
    history = dict(history)
    current_keys: set[str] = set()
    new_rows: list[dict] = []

    for p in current:
        row = p.to_row()
        k = _key(row)
        current_keys.add(k)

        prior = history.get(k)
        if prior is None:
            rec = {f: row.get(f) for f in CARRY_FIELDS}
            rec.update(first_seen=run_date, last_seen=run_date,
                       runs_seen=1, status="new")
            history[k] = rec
            new_rows.append(dict(rec, change="new"))
        else:
            # Same posting, refreshed. Update values, keep first_seen.
            for f in CARRY_FIELDS:
                if row.get(f) not in (None, "", []):
                    prior[f] = row.get(f)
            prior["last_seen"] = run_date
            prior["runs_seen"] = int(prior.get("runs_seen", 1)) + 1
            prior["status"] = "open"

    closed_rows: list[dict] = []
    for k, rec in history.items():
        if k in current_keys:
            continue
        if rec.get("status") != "closed":
            rec["status"] = "closed"
            closed_rows.append(dict(rec, change="closed"))

    return history, {"new": new_rows, "closed": closed_rows}


def rows(history: dict[str, dict]) -> list[dict]:
    """History as a list, newest-first by last_seen."""
    out = list(history.values())
    out.sort(key=lambda r: (str(r.get("last_seen") or ""), str(r.get("institution") or "")),
             reverse=True)
    return out
