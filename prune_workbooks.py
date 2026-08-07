#!/usr/bin/env python3
"""Delete old workbooks in output/, keeping only the most recent N.

`run.py` already does this automatically after every collection run (its
`--keep` flag, default 5). This script exists for when you want to clean up
output/ WITHOUT doing a full run -- e.g. you lowered --keep after the fact
and want the backlog trimmed immediately, you copied workbooks in from
somewhere else, or you just want a manual sweep.

    python prune_workbooks.py              # keep the 5 most recent, delete the rest
    python prune_workbooks.py --keep 10    # keep 10 instead
    python prune_workbooks.py --dry-run    # show what would be deleted, don't delete
    python prune_workbooks.py --dir other/ # prune a different folder

Uses the same src.export.workbook.prune() that run.py calls, so "which files
survive" logic lives in exactly one place.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.export import workbook

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "output"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", type=int, default=5,
                    help="how many workbooks to keep (default 5)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be deleted, don't delete anything")
    ap.add_argument("--dir", default=None,
                    help="directory to prune (default: output/)")
    args = ap.parse_args()

    out_dir = Path(args.dir) if args.dir else OUT_DIR
    if not out_dir.exists():
        print(f"no such directory: {out_dir}", file=sys.stderr)
        return 1

    all_books = sorted(out_dir.glob("workbook_*.xlsx"))
    if not all_books:
        print(f"no workbook_*.xlsx files in {out_dir}")
        return 0

    doomed = workbook.prune(out_dir, keep=args.keep, dry_run=args.dry_run)

    if not doomed:
        print(f"nothing to delete -- {len(all_books)} workbook(s) in {out_dir}, "
              f"keep={args.keep}")
        return 0

    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{verb} {len(doomed)} of {len(all_books)} workbook(s), "
          f"kept the {args.keep} most recent:")
    for b in sorted(doomed):
        print(f"  {b.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
