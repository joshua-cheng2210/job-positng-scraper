from src.filters import annotate
from src.models import Posting
from src.score import rank, score

PROFILE = {"skills": ["Python", "SQL", "React", "AWS", "Linux", "Git"],
           "nice_to_have": ["Docker", "Flask"]}


def mk(title, desc=None):
    return annotate(Posting(institution="U", job_id="1", title=title,
                            url="https://x", platform="t", description=desc))


def test_skill_matches_add_points():
    hi = score(mk("Software Engineer", "Python, SQL and Linux required."), PROFILE)
    lo = score(mk("Software Engineer", "Filing and reception duties."), PROFILE)
    assert hi.score > lo.score
    assert any("Python" in r for r in hi.score_reasons)


def test_years_penalty():
    junior = score(mk("Data Analyst", "0-1 years experience. Python."), PROFILE)
    senior = score(mk("Data Analyst", "Requires 7 years of experience. Python."), PROFILE)
    assert junior.score > senior.score


def test_five_plus_years_penalty_is_ten_points():
    p = score(mk("Data Analyst", "Requires 5 years of experience. Python."), PROFILE)
    assert any("-10.0" in r for r in p.score_reasons)


def test_three_to_four_years_penalty_is_still_two_points():
    p = score(mk("Data Analyst", "Requires 3 years of experience. Python."), PROFILE)
    assert any("-2.0" in r for r in p.score_reasons)


def test_hard_blocker_tanks_the_score():
    p = score(mk("Software Engineer", "Python. Must be a U.S. citizen."), PROFILE)
    assert p.score < 0
    assert any("hard blocker" in r for r in p.score_reasons)


def test_no_stem_opt_is_a_small_penalty_not_a_hard_exclude():
    a = score(mk("Software Engineer", "Python and SQL."), PROFILE)
    b = score(mk("Software Engineer",
                 "Python and SQL. We do not participate in E-Verify."), PROFILE)
    assert b.score == round(a.score - 2.0, 2)
    assert any("-2.0" in r for r in b.score_reasons)


def test_rank_orders_descending():
    ps = [mk("IT Specialist"), mk("Software Engineer", "Python SQL AWS React")]
    out = rank(ps, PROFILE)
    assert out[0].score >= out[1].score
