#!/usr/bin/env python3
"""Pipeline entry point.

    python run.py                     # collect everything enabled
    python run.py --only workday      # one platform
    python run.py --name Minnesota    # substring match on target name
    python run.py --from-cache        # re-filter/re-score without re-collecting
    python run.py --limit 5           # first N targets, for a smoke test
    python run.py --keep 10           # keep the 10 most recent workbooks (default 5)
    python run.py --keep 0            # keep every workbook, never prune
    python run.py --no-enrich         # skip the post-filter detail fetch (faster, less complete)
    python run.py --enrich-workers 20 # more concurrent enrichment requests (default 10)
    python run.py --enrich-limit 100  # only enrich the first 100 unverified survivors

Outputs
    data/postings.json                    this run's raw collection (Cowork handoff)
    data/history.json                     every posting ever seen, deduped
    output/workbook_<date>_<time>.xlsx    one workbook, seven tabs, written every run

Every run writes a new workbook, then prunes output/ down to the --keep most
recent (default 5) so old runs don't pile up. data/postings.json and
data/history.json are never touched by pruning.

Enrichment: the bulk pass (Workday list endpoint, PeopleAdmin Atom feed)
does NOT reliably carry a complete description -- Workday gives none at all
unless fetch_detail is set per-target, and PeopleAdmin's feed omits the
Minimum/Preferred Qualifications fields entirely (confirmed against live
pages). So after filtering, before scoring, run.py fetches one extra detail
request per SURVIVING posting only -- bounded to Shortlist size, not the
full raw collection. Posting.description_scraped is 1 only for rows that
got this real fetch; treat description_scraped=0 rows as unverified,
especially for years-of-experience / skill-keyword conclusions.

Enrichment runs `--enrich-workers` (default 10) requests concurrently and
logs progress every 25 completions -- with hundreds of survivors and a slow
host or two, a sequential run could silently sit for 10+ minutes between log
lines. If a run still feels stuck, watch for the "enrich progress: n/total"
lines; if those keep advancing, it's working, just slow on this network. Use
--enrich-limit to cap how many get fetched if you want a faster, partial run.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import requests

from src import history
from src.adapters import peopleadmin, workday
from src.adapters.base import USER_AGENT
from src.config import build_adapter, load_targets
from src.export import workbook
from src.filters import apply_filters
from src.models import Posting
from src.score import PROFILE, rank

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


_ENRICHERS = {
    "workday": workday.enrich,
    "peopleadmin": peopleadmin.enrich,
}


def _enrich_one(p: Posting) -> bool:
    """Runs in a worker thread -- its own Session, no sharing across threads."""
    fn = _ENRICHERS.get(p.platform)
    if not fn:
        return False
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        return fn(session, p)
    finally:
        session.close()


def enrich_survivors(postings: list[Posting], delay: float,
                      workers: int = 10, limit: int = 0) -> None:
    """One extra detail request per posting that survived filtering -- never
    for the full raw collection. See the module docstring's Enrichment note
    for why the bulk pass alone isn't trustworthy for scoring.

    Runs `workers` requests concurrently (each adapter's enrich() already
    times out on its own -- see workday.enrich / peopleadmin.enrich -- so a
    slow host stalls one worker, not the whole run) and logs progress every
    25 completions so a long run doesn't look hung. `delay` is unused here
    (kept for CLI compatibility) now that requests overlap instead of
    queueing one after another.

    Mutates postings in place. Idempotent: skips anything already marked
    description_scraped=1, so re-running (e.g. --from-cache after a prior
    enriched run) doesn't re-fetch what it already has. If `limit` is set,
    only the first `limit` unenriched postings are attempted -- use this to
    bound worst-case wall-clock time on a huge Shortlist.
    """
    todo = [p for p in postings if not p.description_scraped]
    if limit:
        todo = todo[:limit]
    log.info("enriching %d/%d survivors with %d parallel workers "
              "(already had a verified description: %d)",
              len(todo), len(postings), workers, len(postings) - len(todo))

    done = 0
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_enrich_one, p): p for p in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                if fut.result():
                    done += 1
            except Exception:                              # noqa: BLE001
                log.exception("enrich worker crashed for %s", futures[fut].title)
            if i % 25 == 0 or i == len(todo):
                elapsed = time.monotonic() - start
                log.info("   enrich progress: %d/%d (%d verified, %.0fs elapsed)",
                          i, len(todo), done, elapsed)

    log.info("enrichment: %d/%d survivors got a verified description", done, len(todo))


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
    ap.add_argument("--keep", type=int, default=5,
                    help="how many workbook_*.xlsx to keep in output/ (0 = keep all)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip the post-filter detail fetch (faster, descriptions "
                         "stay incomplete for survivors that weren't already enriched)")
    ap.add_argument("--enrich-workers", type=int, default=10,
                    help="concurrent enrichment requests (default 10)")
    ap.add_argument("--enrich-limit", type=int, default=0,
                    help="cap enrichment to the first N unverified survivors "
                         "(0 = enrich all of them)")
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

    if not args.no_enrich and kept:
        enrich_survivors(kept, args.delay, workers=args.enrich_workers, limit=args.enrich_limit)
        save_cache(raw)     # re-save so postings.json carries the enriched descriptions

    ranked = rank(kept, PROFILE)
    scored_all = rank(list(raw), PROFILE)      # history keeps scores for everything

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

    if args.keep:
        removed = workbook.prune(ROOT / "output", keep=args.keep)
        if removed:
            log.info("pruned %d old workbook(s), kept the %d most recent",
                      len(removed), args.keep)

    print("\nTop 15 by score:")
    for p in shortlist[:15]:
        print(f"  {p.score:6.1f}  {p.institution[:28]:28}  {p.title[:52]}")
    if changes["new"]:
        print(f"\n{len(changes['new'])} new since the last run — see the Changes tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
