"""The one workbook a run produces.

Tabs
    Shortlist      current run, filtered + scored, best first
    All Postings   current run, unfiltered
    History        every posting ever collected, deduped, open + closed
    Changes        new and closed since the previous run
    Summary        counts by institution / portal / state / sponsorship
    Run Stats      what the filters dropped and why

Filename is workbook_<date>_<time>.xlsx, so runs never overwrite each other.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from ..models import Posting
from . import style

BULKY = {"description", "raw"}          # too wide for the overview tabs


def _counts(rows: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(field) or "(blank)")
        out[k] = out.get(k, 0) + 1
    return out


def filename(out_dir: str | Path, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    return Path(out_dir) / f"workbook_{when:%Y-%m-%d_%H%M%S}.xlsx"


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
