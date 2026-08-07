#!/usr/bin/env python3
"""Pipeline entry point.

    python run.py                     # collect everything enabled
    python run.py --only workday      # one platform
    python run.py --name Minnesota    # substring match on target name
    python run.py --from-cache        # re-filter/re-score without re-collecting
    python run.py --limit 5           # first N targets, for a smoke test

Outputs
    data/postings.json                    this run's raw collection (Cowork handoff)
    data/history.json                     every posting ever seen, deduped
    output/workbook_<date>_<time>.xlsx    one workbook, six tabs, never overwritten
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from src import history
from src.config import build_adapter, load_targets
from src.export import workbook
from src.filters import apply_filters
from src.models import Posting
from src.score import load_profile, rank

ROOT = Path(__file__).parent
CACHE = ROOT / "data" / "postings.json"
HISTORY = ROOT / "data" / "history.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run")


def collect(args) -> list[Posting]:
    targets = load_targets(ROOT / "targets.yaml")
    selected = []
    for t in targets:
        if not t.get("enabled", True):
            continue
        if args.only and t.get("platform") != args.only:
            continue
        if args.name and args.name.lower() not in t["name"].lower():
            continue
        selected.append(t)
    if args.limit:
        selected = selected[: args.limit]

    log.info("collecting from %d targets", len(selected))
    out: list[Posting] = []
    for t in selected:
        adapter = build_adapter(t, delay=args.delay)
        if adapter is None:
            continue
        try:
            got = adapter.fetch()
            log.info("%-45s %4d postings", t["name"][:45], len(got))
            out.extend(got)
        except Exception:                              # noqa: BLE001
            log.exception("%s: collection failed", t["name"])
    return out


def save_cache(postings: list[Posting]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps([p.to_row() for p in postings], indent=1, default=str),
        encoding="utf-8",
    )
    log.info("wrote %s (%d postings)", CACHE, len(postings))


def load_cache() -> list[Posting]:
    rows = json.loads(CACHE.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        r.pop("score_reasons", None)
        r.pop("hard_blockers", None)
        r.pop("score", None)
        for k in ("posted_date", "close_date"):
            r[k] = date.fromisoformat(r[k]) if r.get(k) else None
        out.append(Posting(**r))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="platform: workday | peopleadmin | pageup | umn")
    ap.add_argument("--name", help="substring match on target name")
    ap.add_argument("--limit", type=int, help="only the first N targets")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--from-cache", action="store_true",
                    help="skip collection, re-filter data/postings.json")
    ap.add_argument("--top", type=int, default=0,
                    help="write only the top N scored rows (0 = all)")
    args = ap.parse_args()

    if args.from_cache:
        if not CACHE.exists():
            log.error("no cache at %s -- run a collection first", CACHE)
            return 1
        raw = load_cache()
        log.info("loaded %d postings from cache", len(raw))
    else:
        raw = collect(args)
        if not raw:
            log.error("collected nothing. Check targets.yaml and the adapter logs.")
            return 1
        save_cache(raw)

    kept, stats = apply_filters(raw)
    stats["collected_raw"] = len(raw)
    log.info("filter: %d raw -> %d kept", len(raw), len(kept))
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        log.info("   %-40s %d", k, v)

    profile = load_profile(ROOT / "profile.yaml")
    ranked = rank(kept, profile)
    scored_all = rank(list(raw), profile)      # history keeps scores for everything

    # Fold this run into the cumulative history BEFORE trimming to --top, so
    # nothing drops out of the archive just for falling below the cutoff.
    hist = history.load(HISTORY)
    hist, changes = history.update(hist, scored_all)
    history.save(hist, HISTORY)
    log.info("history: %d unique postings (%d new, %d closed this run)",
             len(hist), len(changes["new"]), len(changes["closed"]))

    shortlist = ranked[: args.top] if args.top else ranked

    out = workbook.filename(ROOT / "output")
    workbook.write(
        out,
        shortlist=shortlist,
        all_postings=scored_all,
        history_rows=history.rows(hist),
        changes=changes,
        stats=stats,
    )
    log.info("wrote %s", out)

    print("\nTop 15 by score:")
    for p in shortlist[:15]:
        print(f"  {p.score:6.1f}  {p.institution[:28]:28}  {p.title[:52]}")
    if changes["new"]:
        print(f"\n{len(changes['new'])} new since the last run — see the Changes tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
