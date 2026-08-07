"""PageUp adapter -- STARTER, not yet verified against a live feed.

Targets: University of Oregon, Michigan State, Rutgers, UMBC, Virginia Tech.

PageUp career sites live at {host}/cw/en-us/listing/ and most tenants expose a
JSON search endpoint alongside the HTML. The exact path varies by tenant and by
PageUp version, which is why this is a starter rather than a finished adapter.

TO FINISH THIS (30-45 minutes)
------------------------------
1. Open a PageUp site, e.g. https://careers.uoregon.edu/cw/en-us/listing/
2. DevTools -> Network -> filter XHR -> reload and page through the listings.
3. You are looking for a request to one of (in rough order of likelihood):
       /cw/en-us/search                  (POST, JSON body)
       /cw/en-us/listing/?page=2&...     (GET, returns HTML fragment)
       /search/?q=&startrow=0            (GET, JSON)
4. Copy the request as cURL, confirm the response shape, then fill in
   _search_url / _parse below and delete this block.
5. If the tenant exposes NO JSON endpoint, fall back to parsing the HTML
   listing -- rows are reliably <article class="cw-listing-item"> or similar.
   Prefer the JSON path; HTML scraping breaks on every redesign.

Until then run.py logs a warning and skips PageUp targets rather than
pretending it collected them.
"""
from __future__ import annotations

import logging

from ..models import Posting
from .base import Adapter

log = logging.getLogger(__name__)


class PageUpAdapter(Adapter):
    platform = "pageup"

    def __init__(self, target, **kw):
        super().__init__(target, **kw)
        self.host = target["host"].rstrip("/")

    @property
    def _listing_url(self) -> str:
        return f"{self.host}/cw/en-us/listing/"

    def fetch(self) -> list[Posting]:
        log.warning(
            "%s: PageUp adapter is a stub -- see src/adapters/pageup.py for the "
            "30-minute DevTools recipe to finish it. Skipping.",
            self.name,
        )
        return []

    # def _parse(self, payload: dict) -> list[Posting]:
    #     out = []
    #     for row in payload["jobs"]:
    #         out.append(Posting(
    #             job_id=str(row["id"]),
    #             title=row["title"],
    #             url=f"{self.host}{row['url']}",
    #             location=row.get("location"),
    #             department=row.get("department"),
    #             **self._base_fields(),
    #         ))
    #     return out
