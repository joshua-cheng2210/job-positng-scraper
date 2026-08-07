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


def test_description_scraped_column_survives_the_bulky_drop(tmp_path):
    out = tmp_path / "wb.xlsx"
    scraped = mk("A U", "1", "Software Engineer")
    scraped.description_scraped = 1
    unscraped = mk("A U", "2", "Data Analyst")
    unscraped.description_scraped = 0
    workbook.write(out, shortlist=[scraped, unscraped], all_postings=[scraped, unscraped],
                   history_rows=[], changes={"new": [], "closed": []})
    ws = load_workbook(out)["Shortlist"]
    headers = [ws.cell(row=4, column=c).value for c in range(1, ws.max_column + 1)]
    assert "Description Verified?" in headers
    col = headers.index("Description Verified?") + 1
    values = {ws.cell(row=r, column=col).value for r in (5, 6)}
    assert values == {1, 0}


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


def _touch_workbooks(out_dir, stamps):
    for s in stamps:
        (out_dir / f"workbook_{s}.xlsx").write_bytes(b"")


def test_prune_keeps_only_the_newest_n(tmp_path):
    _touch_workbooks(tmp_path, [
        "2026-08-01_120000", "2026-08-02_120000", "2026-08-03_120000",
        "2026-08-04_120000", "2026-08-05_120000", "2026-08-06_120000",
    ])
    deleted = workbook.prune(tmp_path, keep=5)
    remaining = sorted(p.name for p in tmp_path.glob("workbook_*.xlsx"))
    assert len(remaining) == 5
    assert "workbook_2026-08-01_120000.xlsx" not in remaining
    assert len(deleted) == 1


def test_prune_ignores_non_workbook_files(tmp_path):
    _touch_workbooks(tmp_path, ["2026-08-01_120000", "2026-08-02_120000"])
    (tmp_path / "postings.json").write_text("[]")
    workbook.prune(tmp_path, keep=1)
    assert (tmp_path / "postings.json").exists()
    assert len(list(tmp_path.glob("workbook_*.xlsx"))) == 1


def test_prune_keep_zero_or_under_limit_deletes_nothing(tmp_path):
    _touch_workbooks(tmp_path, ["2026-08-01_120000", "2026-08-02_120000"])
    deleted = workbook.prune(tmp_path, keep=5)
    assert deleted == []
    assert len(list(tmp_path.glob("workbook_*.xlsx"))) == 2


def test_prune_dry_run_reports_but_does_not_delete(tmp_path):
    _touch_workbooks(tmp_path, [
        "2026-08-01_120000", "2026-08-02_120000", "2026-08-03_120000",
    ])
    would_delete = workbook.prune(tmp_path, keep=1, dry_run=True)
    assert len(would_delete) == 2
    assert len(list(tmp_path.glob("workbook_*.xlsx"))) == 3   # nothing actually removed
