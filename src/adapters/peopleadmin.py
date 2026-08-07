"""PeopleAdmin adapter.

Covers UNL, UNC-Chapel Hill, University of Utah, NC State, NDSU, Indiana
academic postings, and most other .peopleadmin.com / employment.<school>.edu
portals.

Endpoint (verified live 2026-07-28 against employment.unl.edu)
--------------------------------------------------------------
GET {base}/postings/search.atom  ->  Atom 1.0 XML

    <entry>
      <id>https://employment.unl.edu/postings/101479</id>
      <published>2026-07-28T16:34:58-05:00</published>
      <updated>...</updated>
      <link rel="alternate" href="https://employment.unl.edu/postings/101479"/>
      <title>Extension Instructor ...</title>
      <content>&lt;div&gt; ...full HTML job description... &lt;/div&gt;</content>
      <author><name>Educational Psychology-1047</name></author>
    </entry>

Why this adapter is cheap: <content> carries the ENTIRE job description, so
sponsorship scanning works straight off the feed with no per-posting detail
request. One HTTP call gets you everything.

Known limitation: the Atom entry has no location field. Location stays None
unless you fetch the HTML posting page. Left unfetched on purpose -- for a
single-campus portal the location is the campus, which targets.yaml already
records as `default_location`.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from xml.etree import ElementTree as ET

from ..models import Posting
from .base import Adapter

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"
_ID_TAIL = re.compile(r"/postings/(\d+)")
# "Educational Psychology-1047" -> "Educational Psychology"
_DEPT_CODE = re.compile(r"-\d+$")


def _text(node, tag: str) -> str | None:
    el = node.find(ATOM + tag)
    return el.text if el is not None else None


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


class PeopleAdminAdapter(Adapter):
    platform = "peopleadmin"

    def __init__(self, target, **kw):
        super().__init__(target, **kw)
        self.base = target["base_url"].rstrip("/")
        self.default_location = target.get("default_location")

    @property
    def _feed_url(self) -> str:
        return f"{self.base}/postings/search.atom"

    def fetch(self) -> list[Posting]:
        xml = self._get(self._feed_url).text
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            log.error("%s: feed at %s is not valid XML", self.name, self._feed_url)
            return []

        postings: list[Posting] = []
        for entry in root.findall(ATOM + "entry"):
            try:
                postings.append(self._to_posting(entry))
            except Exception:                          # noqa: BLE001
                log.exception("%s: could not parse an entry", self.name)

        log.info("%s: %d postings from Atom feed", self.name, len(postings))
        return postings

    def _to_posting(self, entry) -> Posting:
        raw_id = _text(entry, "id") or ""
        m = _ID_TAIL.search(raw_id)

        link_el = entry.find(ATOM + "link")
        url = link_el.get("href") if link_el is not None else raw_id

        author = entry.find(ATOM + "author")
        dept = None
        if author is not None:
            dept = _text(author, "name")
            if dept:
                dept = _DEPT_CODE.sub("", dept).strip()

        return Posting(
            job_id=m.group(1) if m else raw_id,
            title=_text(entry, "title") or "",
            url=url,
            department=dept,
            location=self.default_location,
            posted_date=_parse_dt(_text(entry, "published")),
            description=_text(entry, "content"),
            raw={"atom_id": raw_id},
            **self._base_fields(),
        )
