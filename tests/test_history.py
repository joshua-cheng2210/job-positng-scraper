"""History accumulation: the guarantee that a new workbook never loses old data."""
from datetime import date

from src import history
from src.models import Posting


def mk(inst, jid, title="Software Engineer", score=1.0):
    p = Posting(institution=inst, job_id=jid, title=title,
                url=f"https://x/{jid}", platform="workday")
    p.score = score
    return p


def test_first_run_marks_everything_new():
    hist, changes = history.update({}, [mk("A U", "1"), mk("B U", "2")],
                                   run_date=date(2026, 8, 1))
    assert len(hist) == 2
    assert len(changes["new"]) == 2
    assert changes["closed"] == []
    assert all(r["first_seen"] == "2026-08-01" for r in hist.values())


def test_same_posting_twice_is_not_duplicated():
    hist, _ = history.update({}, [mk("A U", "1")], run_date=date(2026, 8, 1))
    hist, changes = history.update(hist, [mk("A U", "1")], run_date=date(2026, 8, 2))
    assert len(hist) == 1
    assert changes["new"] == []
    rec = next(iter(hist.values()))
    assert rec["first_seen"] == "2026-08-01"     # preserved
    assert rec["last_seen"] == "2026-08-02"      # advanced
    assert rec["runs_seen"] == 2


def test_missing_posting_is_closed_not_deleted():
    hist, _ = history.update({}, [mk("A U", "1"), mk("B U", "2")],
                             run_date=date(2026, 8, 1))
    hist, changes = history.update(hist, [mk("A U", "1")], run_date=date(2026, 8, 2))
    assert len(hist) == 2                         # nothing lost
    assert len(changes["closed"]) == 1
    closed = [r for r in hist.values() if r["status"] == "closed"]
    assert closed[0]["institution"] == "B U"


def test_closed_posting_only_reported_once():
    hist, _ = history.update({}, [mk("A U", "1"), mk("B U", "2")], run_date=date(2026, 8, 1))
    hist, _ = history.update(hist, [mk("A U", "1")], run_date=date(2026, 8, 2))
    hist, changes = history.update(hist, [mk("A U", "1")], run_date=date(2026, 8, 3))
    assert changes["closed"] == []                # already known to be closed


def test_reappearing_posting_flips_back_to_open():
    hist, _ = history.update({}, [mk("A U", "1")], run_date=date(2026, 8, 1))
    hist, _ = history.update(hist, [], run_date=date(2026, 8, 2))
    assert next(iter(hist.values()))["status"] == "closed"
    hist, _ = history.update(hist, [mk("A U", "1")], run_date=date(2026, 8, 3))
    assert next(iter(hist.values()))["status"] == "open"


def test_updated_values_overwrite_but_blanks_do_not():
    hist, _ = history.update({}, [mk("A U", "1", title="Old Title")],
                             run_date=date(2026, 8, 1))
    p = mk("A U", "1", title="New Title")
    p.department = None                           # blank must not wipe a known value
    hist, _ = history.update(hist, [p], run_date=date(2026, 8, 2))
    rec = next(iter(hist.values()))
    assert rec["title"] == "New Title"


def test_round_trip_through_disk(tmp_path):
    path = tmp_path / "history.json"
    hist, _ = history.update({}, [mk("A U", "1")], run_date=date(2026, 8, 1))
    history.save(hist, path)
    assert history.load(path) == hist


def test_load_missing_or_corrupt_file_is_safe(tmp_path):
    assert history.load(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert history.load(bad) == {}
