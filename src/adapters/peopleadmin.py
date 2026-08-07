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

The docstring used to claim <content> carries the ENTIRE job description --
that was wrong. Confirmed by comparing scraped output against the live
posting pages for UNL, Utah, and NC State: <content> only carries the first
field (Description of Work / Job Summary / Essential Job Duties). Everything
after that -- Minimum Required Qualifications, Preferred Qualifications,
salary -- is a separate PeopleAdmin field that never makes it into the Atom
feed. That's exactly where "N years required" and skill requirements live,
so the bulk-pass description alone is not reliable for filtering/scoring.
enrich() below fetches the actual HTML page (`<div id="form_view">`) and
pulls every <th>/<td> field row PeopleAdmin renders, not just the feed's
excerpt. Called by run.py on survivors only, same reasoning as Workday's
detail fetch.

Known limitation: the Atom entry has no location field. Location stays None
unless enrich() runs (the HTML page doesn't reliably carry it either for
every school) -- for a single-campus portal the location is the campus,
which targets.yaml already records as `default_location`.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from xml.etree import ElementTree as ET

from ..models import Posting, _clean
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


# The qualifications tables always sit between the id="form_view" wrapper and
# whatever section comes after it -- named differently per school
# ("Supplemental Questions", "Posting Specific Questions", "Applicant
# Documents", "Required Documents") but always one of these four. If none of
# them match, fall back to scanning the whole page rather than finding
# nothing -- worst case a few harmless extra rows from the questions section.
_FORM_VIEW = re.compile(
    r'<div id="form_view">(.*?)<h2 class="tab">\s*'
    r'(?:Supplemental|Posting Specific|Applicant Documents|Required Documents)',
    re.S | re.I,
)
# Every field PeopleAdmin renders is a <tr><th>Label</th><td>Value</td></tr>.
# This is the one thing the live pages have in common across every school
# checked (UNL, Utah, NC State) despite very different field names.
_TABLE_ROW = re.compile(
    r"<tr>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*</tr>",
    re.S | re.I,
)


def enrich(session, p: Posting, timeout: float = 12.0) -> bool:
    """Fetch the posting's own HTML page and pull every field-by-field row
    PeopleAdmin renders -- Minimum Required Qualifications, Preferred
    Qualifications, salary, etc. -- none of which are in the Atom feed's
    <content>. Meant to be called by run.py on postings that survived
    filtering, not the full bulk pass. Returns True and sets
    p.description_scraped=1 only if it actually found field rows to use.

    `session` is anything with a requests.Session-shaped .get(url, timeout=).
    """
    if not p.url:
        return False
    try:
        resp = session.get(p.url, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
    except Exception:                                 # noqa: BLE001
        log.warning("enrich fetch failed for %s (%s)", p.title, p.url)
        return False

    m = _FORM_VIEW.search(html)
    body = m.group(1) if m else html
    rows = _TABLE_ROW.findall(body)
    if not rows:
        return False

    parts = []
    for label, value in rows:
        label = (_clean(label) or "").rstrip(":")
        value = _clean(value) or ""
        if value:
            parts.append(f"{label}: {value}")
    if not parts:
        return False

    p.description = "\n".join(parts)
    p.description_scraped = 1
    return True
