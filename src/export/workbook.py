"""The one workbook a run produces.

Tabs
    Shortlist      current run, filtered + scored, best first
    Institutions   Shortlist grouped by institution, ranked by a composite score
    All Postings   current run, unfiltered
    History        every posting ever collected, deduped, open + closed
    Changes        new and closed since the previous run
    Summary        counts by institution / portal / state / sponsorship
    Run Stats      what the filters dropped and why

Filename is workbook_<date>_<time>.xlsx, so runs never overwrite each other.
`prune()` caps how many of these pile up in output/ -- run.py calls it after
every write to keep only the most recent 5.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from ..models import Posting
from . import style

log = logging.getLogger(__name__)

BULKY = {"description", "raw"}          # too wide for the overview tabs


def _counts(rows: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(field) or "(blank)")
        out[k] = out.get(k, 0) + 1
    return out


def institution_rankings(rows: list[dict]) -> list[dict]:
    """Group Shortlist rows by institution and rank by a composite that
    rewards both QUALITY (top-3 average score) and QUANTITY (more postings,
    diminishing after 5 -- via sqrt so a 6th or 60th posting barely moves the
    needle once a school has already proven it has real openings). This is
    deliberate: a school with one lucky 11.5 and nothing else shouldn't
    outrank a school with five solid 7s.

    composite = top3_avg_score * sqrt(min(postings, 5))

    verified_pct matters as much as the score -- see Posting.description_scraped.
    A school at 0% verified means none of its scores are backed by a real
    fetched description yet; don't prioritize it over a lower-scoring but
    fully-verified school until enrichment actually runs for it.
    """
    by_inst: dict[str, list[dict]] = {}
    for r in rows:
        by_inst.setdefault(r.get("institution") or "(unknown)", []).append(r)

    out: list[dict] = []
    for inst, group in by_inst.items():
        scores = [float(r.get("score") or 0.0) for r in group]
        n = len(group)
        top3 = sorted(scores, reverse=True)[:3]
        top3_avg = sum(top3) / len(top3) if top3 else 0.0
        verified = sum(1 for r in group if r.get("description_scraped") == 1)

        out.append({
            "institution": inst,
            "postings": n,
            "max_score": round(max(scores), 2) if scores else 0.0,
            "avg_score": round(sum(scores) / n, 2) if n else 0.0,
            "top3_avg_score": round(top3_avg, 2),
            "verified_pct": round(verified / n * 100) if n else 0,
            "positive_sponsorship": sum(1 for r in group
                                        if r.get("sponsorship_flag") == "h1b_possible"),
            "no_sponsorship": sum(1 for r in group
                                  if r.get("sponsorship_flag") == "no_sponsorship_any"),
            "composite": round(top3_avg * math.sqrt(min(n, 5)), 2),
        })

    out.sort(key=lambda r: r["composite"], reverse=True)
    return out


def filename(out_dir: str | Path, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    return Path(out_dir) / f"workbook_{when:%Y-%m-%d_%H%M%S}.xlsx"


def prune(out_dir: str | Path, keep: int = 5, dry_run: bool = False) -> list[Path]:
    """Delete all but the `keep` most recent workbook_*.xlsx files in out_dir.

    The timestamp is in the filename (workbook_<date>_<time>.xlsx), so a plain
    reverse-alphabetical sort is also a reverse-chronological sort -- no stat()
    call needed. Returns the paths that were (or, with dry_run=True, would be)
    deleted, so the caller can log them; deletion failures (e.g. a file open
    in Excel) are logged and skipped rather than raised, so pruning never
    fails a run.
    """
    out_dir = Path(out_dir)
    books = sorted(out_dir.glob("workbook_*.xlsx"), reverse=True)
    doomed = books[keep:]
    if dry_run:
        return doomed

    deleted: list[Path] = []
    for book in doomed:
        try:
            book.unlink()
            deleted.append(book)
        except OSError as exc:
            log.warning("could not delete %s: %s", book, exc)
    return deleted


def write(out_path: str | Path, *, shortlist: list[Posting], all_postings: list[Posting],
          history_rows: list[dict], changes: dict[str, list[dict]],
          stats: dict[str, int] | None = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    short_rows = [p.to_row() for p in shortlist]
    all_rows = [p.to_row() for p in all_postings]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    wb = Workbook()
    wb.remove(wb.active)

    style.write_table(
        wb.create_sheet("Shortlist"),
        style.sort_rows(short_rows, "score", desc=True),
        title=f"Shortlist — {len(short_rows)} postings — {stamp}",
        subtitle=("Survived the filters, sorted by match score. Green = positive "
                  "sponsorship language, amber = declines STEM OPT (still applicable "
                  "via cap-exempt H-1B), red = no sponsorship. A Blocker usually means skip."),
        drop=BULKY,
    )

    rankings = institution_rankings(short_rows)
    style.write_table(
        wb.create_sheet("Institutions"),
        rankings,
        title=f"Institution Rankings — {len(rankings)} institutions — {stamp}",
        subtitle=("Composite = top-3 average score x sqrt(min(postings, 5)) -- rewards "
                  "a school with several solid postings over one lucky outlier. "
                  "Verified % near 0 means don't trust that school's scores yet -- "
                  "enrichment hasn't confirmed the descriptions behind them."),
    )

    style.write_table(
        wb.create_sheet("All Postings"),
        all_rows,
        title=f"All postings collected — {len(all_rows)} rows — {stamp}",
        subtitle="Unfiltered. Use this to check why something never reached the Shortlist.",
        drop=BULKY,
    )

    style.write_table(
        wb.create_sheet("History"),
        history_rows,
        title=f"History — {len(history_rows)} unique postings ever seen",
        subtitle=("Carried forward across every run and deduplicated by institution + job ID. "
                  "status=closed means it was not in the latest run."),
        drop=BULKY,
    )

    change_rows = changes.get("new", []) + changes.get("closed", [])
    style.write_table(
        wb.create_sheet("Changes"),
        change_rows,
        title=f"Changes this run — {len(changes.get('new', []))} new, "
              f"{len(changes.get('closed', []))} closed",
        subtitle="Only what moved since the previous run. Empty on a first run.",
        drop=BULKY,
    )

    style.write_counts(
        wb.create_sheet("Summary"),
        f"Summary — {stamp}",
        [
            ("Shortlist by institution", _counts(short_rows, "institution")),
            ("Shortlist by sponsorship flag", _counts(short_rows, "sponsorship_flag")),
            ("All postings by portal", _counts(all_rows, "platform")),
            ("All postings by state", _counts(all_rows, "state")),
            ("History by status", _counts(history_rows, "status")),
        ],
    )

    if stats:
        style.write_counts(wb.create_sheet("Run Stats"), f"Run stats — {stamp}",
                           [("Filter outcome", dict(stats))])

    wb.save(out_path)
    return out_path
