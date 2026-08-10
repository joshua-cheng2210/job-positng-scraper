"""run.py: concurrent enrichment of filtered survivors."""
import time

import run
from src.models import Posting


def _posting(platform="workday", jid="1", scraped=0):
    p = Posting(institution="U", job_id=jid, title="Software Engineer",
                url=f"https://x/{jid}", platform=platform, state="MN")
    p.description_scraped = scraped
    return p


def test_enrich_survivors_calls_every_unenriched_posting(monkeypatch):
    postings = [_posting(jid=str(i)) for i in range(6)]
    seen = []

    def fake_enrich_one(p):
        seen.append(p.job_id)
        p.description_scraped = 1
        return True

    monkeypatch.setattr(run, "_enrich_one", fake_enrich_one)
    run.enrich_survivors(postings, delay=0, workers=4)

    assert sorted(seen) == [str(i) for i in range(6)]
    assert all(p.description_scraped == 1 for p in postings)


def test_enrich_survivors_skips_already_scraped(monkeypatch):
    postings = [_posting(jid="1", scraped=1), _posting(jid="2", scraped=0)]
    seen = []

    def fake_enrich_one(p):
        seen.append(p.job_id)
        return True

    monkeypatch.setattr(run, "_enrich_one", fake_enrich_one)
    run.enrich_survivors(postings, delay=0, workers=4)

    assert seen == ["2"]


def test_enrich_survivors_respects_limit(monkeypatch):
    postings = [_posting(jid=str(i)) for i in range(10)]
    seen = []

    def fake_enrich_one(p):
        seen.append(p.job_id)
        return True

    monkeypatch.setattr(run, "_enrich_one", fake_enrich_one)
    run.enrich_survivors(postings, delay=0, workers=4, limit=3)

    assert len(seen) == 3


def test_enrich_survivors_runs_concurrently_not_sequentially(monkeypatch):
    """The whole point of this rewrite: N slow requests should take roughly
    (N / workers) * per-call-time, not N * per-call-time."""
    postings = [_posting(jid=str(i)) for i in range(8)]

    def fake_enrich_one(p):
        time.sleep(0.2)
        return True

    monkeypatch.setattr(run, "_enrich_one", fake_enrich_one)
    start = time.monotonic()
    run.enrich_survivors(postings, delay=0, workers=4)
    elapsed = time.monotonic() - start

    # sequential would be 8 * 0.2 = 1.6s; 4 workers should land near 2 * 0.2 = 0.4s
    assert elapsed < 1.0


def test_enrich_survivors_one_crashing_worker_does_not_kill_the_run(monkeypatch):
    postings = [_posting(jid=str(i)) for i in range(4)]

    def fake_enrich_one(p):
        if p.job_id == "1":
            raise ConnectionError("boom")
        return True

    monkeypatch.setattr(run, "_enrich_one", fake_enrich_one)
    # should not raise
    run.enrich_survivors(postings, delay=0, workers=4)


def _ranked(jid, title, desc="", hard_blockers=None):
    p = Posting(institution="U", job_id=jid, title=title, url=f"https://x/{jid}",
                platform="workday", state="MN", description=desc)
    p.hard_blockers = hard_blockers or []
    return p


def test_split_shortlist_separates_part_time_and_temporary():
    full_time = _ranked("1", "Software Engineer")
    part_time = _ranked("2", "Data Analyst", desc="This is a part-time position.")
    temp = _ranked("3", "Research Assistant", desc="This is a temporary, grant-funded role.")

    shortlist, part_time_list = run.split_shortlist([full_time, part_time, temp])

    assert shortlist == [full_time]
    assert part_time_list == [part_time, temp]


def test_split_shortlist_ignores_generic_benefits_boilerplate():
    """'Perks and Benefit eligibility is based on Part-Time or Full-Time
    Employment status' mentions part-time without describing THIS posting's
    employment type -- shouldn't route an otherwise full-time role away."""
    full_time = _ranked("1", "Business Intelligence Analyst",
                         desc="Perks and Benefit eligibility is based on Part-Time or "
                              "Full-Time Employment status. Great benefits await.")
    genuinely_part_time = _ranked("2", "Data Analyst",
                                   desc="This is a part-time position, 20 hours/week.")

    shortlist, part_time_list = run.split_shortlist([full_time, genuinely_part_time])

    assert shortlist == [full_time]
    assert part_time_list == [genuinely_part_time]


def test_split_shortlist_drops_hard_blockered_postings_from_both_tabs():
    blocked_full = _ranked("1", "Software Engineer", hard_blockers=["US citizenship required"])
    blocked_part_time = _ranked("2", "Data Analyst", desc="Part-time role.",
                                 hard_blockers=["Student status required"])
    clean = _ranked("3", "Data Engineer")

    shortlist, part_time_list = run.split_shortlist([blocked_full, blocked_part_time, clean])

    assert shortlist == [clean]
    assert part_time_list == []
