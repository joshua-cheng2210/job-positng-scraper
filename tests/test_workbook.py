"""The six-tab workbook."""
from datetime import datetime

from openpyxl import load_workbook

from src.export import workbook
from src.models import Posting


def mk(inst, jid, title, score=0.0, flag="unknown"):
    p = Posting(institution=inst, job_id=jid, title=title,
                url=f"https://x/{jid}", platform="workday", state="MN")
    p.score = score
    p.sponsorship_flag = flag
    return p


def test_filename_is_timestamped(tmp_path):
    f = workbook.filename(tmp_path, datetime(2026, 8, 6, 18, 38, 3))
    assert f.name == "workbook_2026-08-06_183803.xlsx"


def test_two_runs_never_collide(tmp_path):
    a = workbook.filename(tmp_path, datetime(2026, 8, 6, 18, 0, 0))
    b = workbook.filename(tmp_path, datetime(2026, 8, 6, 18, 0, 1))
    assert a != b


def test_all_six_tabs_present(tmp_path):
    out = tmp_path / "wb.xlsx"
    short = [mk("A U", "1", "Software Engineer", 12.0, "h1b_possible")]
    every = short + [mk("A U", "2", "Groundskeeper")]
    hist = [{"institution": "A U", "job_id": "1", "title": "Software Engineer",
             "status": "open", "first_seen": "2026-08-01", "last_seen": "2026-08-06",
             "runs_seen": 3}]
    changes = {"new": [dict(hist[0], change="new")], "closed": []}

    workbook.write(out, shortlist=short, all_postings=every,
                   history_rows=hist, changes=changes, stats={"kept": 1})

    wb = load_workbook(out)
    assert wb.sheetnames == ["Shortlist", "All Postings", "History",
                             "Changes", "Summary", "Run Stats"]
    assert wb["Shortlist"].freeze_panes == "A5"
    assert wb["Shortlist"].auto_filter.ref is not None


def test_description_column_dropped_from_overview_tabs(tmp_path):
    out = tmp_path / "wb.xlsx"
    p = mk("A U", "1", "Software Engineer")
    p.description = "x" * 5000
    workbook.write(out, shortlist=[p], all_postings=[p],
                   history_rows=[], changes={"new": [], "closed": []})
    ws = load_workbook(out)["Shortlist"]
    headers = [ws.cell(row=4, column=c).value for c in range(1, ws.max_column + 1)]
    assert "Description" not in headers


def test_empty_tabs_render_without_crashing(tmp_path):
    out = tmp_path / "wb.xlsx"
    workbook.write(out, shortlist=[], all_postings=[],
                   history_rows=[], changes={"new": [], "closed": []})
    wb = load_workbook(out)
    assert wb["Changes"]["A4"].value == "(nothing to show)"
