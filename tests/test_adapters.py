"""Adapter parsing, against payloads captured from the live APIs on 2026-07-28."""
import json

import pytest

from tests.conftest import FIXTURES
from src.adapters.peopleadmin import PeopleAdminAdapter
from src.adapters.workday import WorkdayAdapter, _job_id, _scan_bullets

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
