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
