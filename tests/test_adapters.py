"""Adapter parsing, against payloads captured from the live APIs on 2026-07-28."""
import json

import pytest

from tests.conftest import FIXTURES
from src.adapters import peopleadmin as peopleadmin_mod
from src.adapters import workday as workday_mod
from src.adapters.peopleadmin import PeopleAdminAdapter
from src.adapters.workday import WorkdayAdapter, _job_id, _scan_bullets
from src.models import Posting

MINNSTATE = {
    "name": "Minnesota State", "platform": "workday", "state": "MN",
    "host": "https://minnstate.wd115.myworkdayjobs.com",
    "tenant": "minnstate", "site": "Minnesota_State_Careers",
    "multi_institution": True,
}
WISCONSIN = {
    "name": "Universities of Wisconsin", "platform": "workday", "state": "WI",
    "host": "https://wisconsin.wd1.myworkdayjobs.com",
    "tenant": "wisconsin", "site": "UW_Comprehensives",
    "multi_institution": True,
}
UNL = {
    "name": "UNL", "platform": "peopleadmin", "state": "NE",
    "base_url": "https://employment.unl.edu", "default_location": "Lincoln, NE",
}


class _Resp:
    def __init__(self, payload=None, text=None):
        self._payload, self.text = payload, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _workday(monkeypatch, target, fixture):
    payload = json.loads((FIXTURES / fixture).read_text())
    a = WorkdayAdapter(target, delay=0)
    calls = {"n": 0}

    def fake_post(url, json=None, **kw):
        calls["n"] += 1
        return _Resp(payload if calls["n"] == 1 else {"total": payload["total"],
                                                      "jobPostings": []})
    monkeypatch.setattr(a, "_post", fake_post)
    return a.fetch()


def test_job_id_comes_from_path_not_bulletfields():
    # The whole point: bulletFields differs per tenant, externalPath does not.
    assert _job_id("/job/St-Paul/Network-Engineer_JR0000005315", "x") == "JR0000005315"
    assert _job_id("/job/Milwaukee-WI/App-Dev_JR10012777", "x") == "JR10012777"
    assert _job_id("/job/X/Event-Intern_REQ_0000062018-1", "x") == "REQ_0000062018-1"
    assert _job_id("no-underscore-here", "fallback") == "fallback"


def test_scan_bullets_handles_both_tenant_schemas():
    close, inst = _scan_bullets(["JR0000005305", "2026-08-11", "St. Cloud State University"])
    assert close.isoformat() == "2026-08-11"
    assert inst == "St. Cloud State University"

    close, inst = _scan_bullets(["Application Deadline: 08/02/2026"])
    assert close.isoformat() == "2026-08-02"
    assert inst is None

    assert _scan_bullets(None) == (None, None)
    assert _scan_bullets([]) == (None, None)


def test_workday_minnstate(monkeypatch):
    got = _workday(monkeypatch, MINNSTATE, "workday_minnstate.json")
    assert len(got) == 4
    p = got[0]
    assert p.job_id == "JR0000005315"
    assert p.title == "Information Technology Spec 5 - Network Engineer"
    assert p.location == "St. Paul"
    assert p.close_date.isoformat() == "2026-07-28"
    # multi_institution: employer overrides the tenant-level name
    assert p.institution == "Minnesota State System Office"
    assert p.url.startswith("https://minnstate.wd115.myworkdayjobs.com/Minnesota_State_Careers/job/")


def test_workday_wisconsin_different_bulletfields(monkeypatch):
    got = _workday(monkeypatch, WISCONSIN, "workday_wisconsin.json")
    assert len(got) == 2
    p = got[0]
    assert p.job_id == "JR10012777"
    assert p.close_date.isoformat() == "2026-08-22"
    # no institution in bulletFields here, so the tenant name stands
    assert p.institution == "Universities of Wisconsin"


def test_peopleadmin_atom(monkeypatch):
    xml = (FIXTURES / "peopleadmin_unl.atom").read_text()
    a = PeopleAdminAdapter(UNL, delay=0)
    monkeypatch.setattr(a, "_get", lambda url, **kw: _Resp(text=xml))
    got = a.fetch()

    assert len(got) == 3
    p = got[0]
    assert p.job_id == "101500"
    assert p.title == "Research Software Engineer"
    assert p.department == "Holland Computing Center"      # numeric code stripped
    assert p.location == "Lincoln, NE"
    assert p.posted_date.isoformat() == "2026-07-28"
    assert "Holland Computing Center" in p.description
    assert "<div>" not in p.description                    # HTML stripped
    assert p.description_scraped == 0                      # atom feed only, not enriched


class _FakeSession:
    """Stand-in for requests.Session -- just needs .get(url, timeout=)."""

    def __init__(self, resp=None, raise_exc=None):
        self._resp, self._raise = resp, raise_exc
        self.calls = []

    def get(self, url, timeout=None, **kw):
        self.calls.append(url)
        if self._raise:
            raise self._raise
        return self._resp


def _posting(**kw):
    base = dict(institution="U", job_id="1", title="Software Engineer",
                url="https://x", platform="test")
    base.update(kw)
    return Posting(**base)


def test_workday_enrich_sets_description_scraped():
    p = _posting(platform="workday", raw={"detail_url": "https://x/detail"})
    payload = {"jobPostingInfo": {
        "jobDescription": "Python and AWS required. 2 years experience.",
        "jobReqId": "JR123",
    }}
    session = _FakeSession(resp=_Resp(payload=payload))
    ok = workday_mod.enrich(session, p)
    assert ok is True
    assert p.description_scraped == 1
    assert "Python and AWS" in p.description
    assert p.job_id == "JR123"
    assert session.calls == ["https://x/detail"]


def test_workday_enrich_no_detail_url_is_a_noop():
    p = _posting(platform="workday", raw={})
    session = _FakeSession()
    assert workday_mod.enrich(session, p) is False
    assert p.description_scraped == 0
    assert session.calls == []


def test_workday_enrich_empty_description_not_marked_scraped():
    p = _posting(platform="workday", raw={"detail_url": "https://x/detail"})
    session = _FakeSession(resp=_Resp(payload={"jobPostingInfo": {}}))
    assert workday_mod.enrich(session, p) is False
    assert p.description_scraped == 0


def test_workday_enrich_request_failure_leaves_scraped_at_zero():
    p = _posting(platform="workday", raw={"detail_url": "https://x/detail"})
    session = _FakeSession(raise_exc=ConnectionError("boom"))
    assert workday_mod.enrich(session, p) is False
    assert p.description_scraped == 0


_PEOPLEADMIN_HTML = """
<div id="form_view">
  <table>
    <tr><th>Description of Work</th><td>Build things.</td></tr>
    <tr><th>Minimum Required Qualifications</th>
        <td><ul><li>4 years culinary experience</li></ul></td></tr>
    <tr><th>Preferred Qualifications</th><td>Python, AWS, Docker</td></tr>
  </table>
  <h2 class="tab">Supplemental Questions</h2>
  <p>ignored: 99 years or more</p>
</div>
"""


def test_peopleadmin_enrich_pulls_qualifications_fields():
    p = _posting(platform="peopleadmin", url="https://employment.unl.edu/postings/1")
    session = _FakeSession(resp=_Resp(text=_PEOPLEADMIN_HTML))
    ok = peopleadmin_mod.enrich(session, p)
    assert ok is True
    assert p.description_scraped == 1
    assert "4 years culinary experience" in p.description
    assert "Python, AWS, Docker" in p.description
    # boundary worked: the Supplemental Questions junk did not leak in
    assert "99 years or more" not in p.description


def test_peopleadmin_enrich_no_rows_found_is_a_noop():
    p = _posting(platform="peopleadmin", url="https://employment.unl.edu/postings/1")
    session = _FakeSession(resp=_Resp(text="<html><body>nothing here</body></html>"))
    assert peopleadmin_mod.enrich(session, p) is False
    assert p.description_scraped == 0


def test_peopleadmin_enrich_no_url_is_a_noop():
    p = _posting(platform="peopleadmin", url="")
    session = _FakeSession()
    assert peopleadmin_mod.enrich(session, p) is False
    assert session.calls == []
