"""Workday CXS adapter.

Covers the single biggest slice of US higher-ed hiring: Minnesota State (33
schools), Universities of Wisconsin (12), Penn State (24 campuses), Ohio State,
UMD, WSU, LSU, USNH -- roughly 75 institutions from this one file.

Endpoints (verified live 2026-07-28)
------------------------------------
LIST    POST {host}/wday/cxs/{tenant}/{site}/jobs
        body: {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}
        -> {"total": int,
            "jobPostings": [{"title", "externalPath", "locationsText",
                             "postedOn", "bulletFields": [...],
                             "remoteType"?}],
            "facets": [...]}

DETAIL  GET {host}/wday/cxs/{tenant}/{site}{externalPath}
        -> {"jobPostingInfo": {"id", "title", "jobDescription", "location",
                               "startDate", "endDate", "jobReqId",
                               "externalUrl", "timeType", ...}}

Two gotchas that will bite you if you deviate
---------------------------------------------
1. `limit` is capped at 20. Sending limit=100 returns ZERO postings, not an
   error and not a truncated page -- an empty list. Silent failure. Do not
   "optimise" the page size.
2. `bulletFields` is configured per tenant and is NOT a stable schema.
      MinnState  -> ["JR0000005305", "2026-08-11", "St. Cloud State University"]
      Wisconsin  -> ["Application Deadline: 08/02/2026"]
   Never index into it positionally. The job ID is parsed out of
   `externalPath` instead, which is consistent across every tenant observed.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from ..models import Posting
from .base import Adapter

log = logging.getLogger(__name__)

PAGE_SIZE = 20          # hard API cap -- see module docstring
MAX_PAGES = 200         # safety valve: 4,000 postings per target

# Trailing job id on externalPath. Must survive all three observed shapes:
#   ".../Network-Engineer_JR0000005315"      -> JR0000005315
#   ".../App-Dev_JR10012777"                 -> JR10012777
#   ".../Event-Intern_REQ_0000062018-1"      -> REQ_0000062018-1   (id contains _)
# The optional "[A-Za-z]+_" prefix group is what keeps the Penn State style
# intact; without it the match starts at the LAST underscore and silently
# returns "0000062018-1".
_ID_FROM_PATH = re.compile(r"_((?:[A-Za-z]+_)?[A-Za-z]*\d[\w-]*)$")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_US_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    m = _ISO.search(value)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d").date()
        except ValueError:
            pass
    m = _US_DATE.search(value)
    if m:
        try:
            mo, dy, yr = (int(g) for g in m.groups())
            return date(yr, mo, dy)
        except ValueError:
            pass
    return None


def _job_id(external_path: str, fallback: str) -> str:
    # Only look at the final path segment; earlier segments are location slugs
    # and can contain underscores of their own.
    segment = (external_path or "").rstrip("/").split("/")[-1]
    m = _ID_FROM_PATH.search(segment)
    return m.group(1) if m else fallback


def _scan_bullets(bullets: list[str] | None) -> tuple[date | None, str | None]:
    """bulletFields has no fixed schema, so sniff it instead of indexing.

    Returns (close_date, institution_hint). Either may be None.
    """
    close, inst = None, None
    for b in bullets or []:
        if not isinstance(b, str):
            continue
        d = _parse_date(b)
        if d and close is None:
            close = d
            continue
        # an institution name: has a space, is wordy, isn't a req id, and
        # isn't a bare label like "Application Deadline:" -- some tenants
        # split the label and its date into two separate bulletFields
        # entries instead of one combined string, and a label-only bullet
        # has no digits of its own so _parse_date() can't catch it here.
        if (inst is None and " " in b and not b.rstrip().endswith(":")
                and not re.fullmatch(r"[A-Z]*\d[\w-]*", b)):
            if not _parse_date(b):
                inst = b
    return close, inst


class WorkdayAdapter(Adapter):
    platform = "workday"

    def __init__(self, target, **kw):
        super().__init__(target, **kw)
        self.host = target["host"].rstrip("/")
        self.tenant = target["tenant"]
        self.site = target["site"]
        # Fetching the full description costs one extra request per posting.
        # Default off for the bulk pass; run.py turns it on for survivors only.
        self.want_detail = bool(target.get("fetch_detail", False))

    # -- urls ---------------------------------------------------------------

    @property
    def _list_url(self) -> str:
        return f"{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def _detail_url(self, external_path: str) -> str:
        return f"{self.host}/wday/cxs/{self.tenant}/{self.site}{external_path}"

    def _public_url(self, external_path: str) -> str:
        return f"{self.host}/{self.site}{external_path}"

    # -- fetch --------------------------------------------------------------

    def fetch(self) -> list[Posting]:
        postings: list[Posting] = []
        offset, total, pages = 0, None, 0

        while pages < MAX_PAGES:
            body = {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            }
            data = self._post(self._list_url, json=body).json()
            if total is None:
                total = data.get("total", 0)
                log.info("%s: %s postings reported", self.name, total)

            batch = data.get("jobPostings") or []
            if not batch:
                break

            for jp in batch:
                try:
                    postings.append(self._to_posting(jp))
                except Exception:                     # noqa: BLE001
                    log.exception("%s: could not parse %r", self.name, jp)

            offset += len(batch)
            pages += 1
            done = total is not None and offset >= total
            # Large tenants (Penn State: 1384 postings / 70 pages) paginate
            # for a minute-plus with the default 1s delay, and used to log
            # nothing between "N postings reported" and the final summary --
            # looked hung even though it was working. Print every 5 pages
            # (and the last one) so it's visibly alive.
            if pages % 5 == 0 or done:
                pct = f"{offset / total:.0%}" if total else "?"
                log.info("   %s: %d/%s collected (%s, page %d)",
                          self.name, offset, total, pct, pages)
            if done:
                break
            self._sleep()

        if total is not None and len(postings) < total:
            log.warning(
                "%s: collected %d of %d reported postings",
                self.name, len(postings), total,
            )
        return postings

    def _to_posting(self, jp: dict) -> Posting:
        path = jp.get("externalPath") or ""
        close, inst_hint = _scan_bullets(jp.get("bulletFields"))
        fallback_id = re.sub(r"[^\w-]", "-", jp.get("title", ""))[:40]

        p = Posting(
            job_id=_job_id(path, fallback_id),
            title=jp.get("title", ""),
            url=self._public_url(path),
            location=jp.get("locationsText"),
            close_date=close,
            # A multi-institution tenant (MinnState, UW) puts the real employer
            # in bulletFields. Prefer it over the tenant-level name.
            department=inst_hint,
            raw=jp,
            **self._base_fields(),
        )
        if inst_hint and self.target.get("multi_institution"):
            p.institution = inst_hint

        # Stored unconditionally (cheap: it's just a string) so the standalone
        # enrich() below can fetch detail later for survivors, even when this
        # target doesn't set fetch_detail: true for the bulk pass.
        if path:
            p.raw["detail_url"] = self._detail_url(path)

        if self.want_detail and path:
            self._enrich(p, path)
        return p

    def _enrich(self, p: Posting, external_path: str) -> None:
        """Second request: full description + real dates, during the bulk
        pass itself. Only worth doing here for a target small enough that
        fetch_detail: true is affordable for every posting -- otherwise leave
        it to run.py calling enrich() on survivors after filtering."""
        try:
            info = self._get(self._detail_url(external_path)).json()
        except Exception:                             # noqa: BLE001
            log.warning("%s: detail fetch failed for %s", self.name, external_path)
            return
        _apply_detail(p, info.get("jobPostingInfo") or {})
        self._sleep()


def _apply_detail(p: Posting, info: dict) -> bool:
    """Write a jobPostingInfo payload onto a Posting. Returns True if it
    actually had a description to give (an empty/malformed payload should
    not be recorded as description_scraped=1)."""
    if not info.get("jobDescription"):
        return False
    p.description = info["jobDescription"]
    p.posted_date = _parse_date(info.get("startDate")) or p.posted_date
    p.close_date = _parse_date(info.get("endDate")) or p.close_date
    p.location = info.get("location") or p.location
    p.url = info.get("externalUrl") or p.url
    if info.get("jobReqId"):
        p.job_id = info["jobReqId"]
    p.raw["detail"] = {
        k: info.get(k) for k in ("timeType", "jobReqId", "startDate", "endDate")
    }
    p.description_scraped = 1
    return True


def enrich(session, p: Posting, timeout: float = 12.0) -> bool:
    """Standalone detail fetch for a single posting, using its stored
    detail_url. Meant to be called by run.py on postings that survived
    filtering, so the second request only ever happens for postings worth
    the cost -- see the module docstring's Two-Gotchas note plus
    src/filters.py for the "why enrich after, not before" reasoning.

    `session` is anything with a requests.Session-shaped .get(url, timeout=)
    -- normally a real requests.Session, a fake in tests.
    """
    detail_url = p.raw.get("detail_url") if p.raw else None
    if not detail_url:
        return False
    try:
        resp = session.get(detail_url, timeout=timeout)
        resp.raise_for_status()
        info = (resp.json() or {}).get("jobPostingInfo") or {}
    except Exception:                                 # noqa: BLE001
        log.warning("enrich failed for %s (%s)", p.title, p.url)
        return False
    return _apply_detail(p, info)
