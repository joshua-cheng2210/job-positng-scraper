"""University of Minnesota adapter -- STARTER.

UMN runs PeopleSoft HCM (Oracle) at
https://hr.myu.umn.edu/jobs/ext/ which redirects to
https://hr.myu.umn.edu/psc/hrprd/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL

This is the hardest of the four platforms: PeopleSoft fluid pages are
stateful, use ICSID tokens, and do not offer a clean public JSON API. Two
viable approaches, in order of preference:

A. FIND THE FLUID REST CALL (preferred, ~1 hour)
   PeopleSoft fluid search pages issue POSTs to the same .GBL URL with
   ICAction / ICSID / ICStateNum form fields and return HTML fragments.
   - Load the page, DevTools -> Network -> XHR, click "Search Jobs".
   - Capture ICSID and ICStateNum from the initial page HTML (hidden inputs).
   - Replay the POST with requests.Session() to keep cookies.
   - Parse the returned fragment; job rows carry ids like HRS_SCH_WRK_*.

B. HEADLESS BROWSER FALLBACK (~30 min, more fragile, needs playwright)
   playwright.sync_api -> goto the ext URL -> wait for the results list ->
   page.query_selector_all("[id^='HRS_SCH_WRK_HRS_JO_PST_SEQ']") -> read text.
   Slower and breaks on UI changes, but it works today.

Observed page structure (2026-07-27): results render as a list where each row
shows Title / Job ID / Location / Department / Posted Date, and the location
filter values are Twin Cities, Duluth, Morris, Crookston, Rochester. There were
702-715 open postings, of which ~9 sat in the Information Technology job family
and ~55 in Research.

Worth knowing before you sink time here: per Josh's own portal scan, the UMN IT
postings disclaim STEM OPT sponsorship -- but that does NOT rule UMN out,
because cap-exempt H-1B needs no E-Verify. Build this adapter for the Research
job family as much as for IT.
"""
from __future__ import annotations

import logging

from ...models import Posting
from ..base import Adapter

log = logging.getLogger(__name__)

EXT_URL = "https://hr.myu.umn.edu/jobs/ext/"


class UMNAdapter(Adapter):
    platform = "custom"

    def fetch(self) -> list[Posting]:
        log.warning(
            "%s: UMN PeopleSoft adapter is a stub -- see "
            "src/adapters/custom/umn.py for two implementation routes. Skipping.",
            self.name,
        )
        return []
