from datetime import date

from src.filters import annotate, apply_filters, is_excluded
from src.models import Posting


def mk(title, desc=None, dept=None):
    return Posting(institution="Test U", job_id="1", title=title,
                   url="https://x", platform="test", description=desc,
                   department=dept)


def test_keeps_real_tech_titles():
    for t in ["Software Engineer", "Research Software Engineer",
              "Application Developer", "Data Analyst", "Systems Analyst",
              "Research Professional 1", "Business Intelligence Developer",
              "Information Technology Spec 5 - Network Engineer"]:
        assert is_excluded(mk(t)) is None, t


def test_drops_non_tech():
    for t in ["Assistant Coach - Track & Field", "Chick-fil-A Operations Manager",
              "Electrician", "Snow Plow Operator", "Assistant Professor of Biology",
              "Career Coach", "Regional Development Officer"]:
        assert is_excluded(mk(t)) is not None, t


def test_drops_senior_titles():
    for t in ["Senior Data Engineer", "Lead Software Engineer",
              "Principal Systems Architect", "IT Director",
              "Database Administrator III"]:
        assert is_excluded(mk(t)) is not None, t


def test_hard_blockers_detected():
    p = annotate(mk("Systems Analyst", "Must be a U.S. citizen for this role."))
    assert "US citizenship required" in p.hard_blockers

    p = annotate(mk("Software Engineer", "Requires an active security clearance."))
    assert "Security clearance required" in p.hard_blockers


def test_sponsorship_positive():
    p = annotate(mk("Software Engineer",
                    "UNL may be able to sponsor temporary work authorization (e.g., H-1B)."))
    assert p.sponsorship_flag == "h1b_possible"


def test_sponsorship_not_eligible_for_visa_phrasing():
    """'not eligible for ... sponsorship' is a different grammatical shape than
    'not eligible TO sponsor' -- both need to be caught."""
    p = annotate(mk("AI Engineer",
                    "This position is not eligible for work visa sponsorship."))
    assert p.sponsorship_flag == "no_sponsorship_any"


def test_drops_high_experience_requirement():
    """A penalty in score.py alone doesn't stop a heavily-keyword-matched
    posting from still floating to the top -- this needs a hard exclude."""
    p = mk("AI/ML Engineer",
           "Requires a bachelor's + 6 years or a master's + 4 years of "
           "directly related work experience. Advanced role requires "
           "bachelor's + 8 years or master's + 6 years.")
    reason = is_excluded(p)
    assert reason is not None
    assert "8+ years" in reason


def test_keeps_entry_level_years():
    p = mk("Software Engineer", "0-1 years of experience required. New grads welcome.")
    assert is_excluded(p) is None


def test_keeps_years_at_threshold():
    p = mk("Software Engineer", "4 years of experience preferred.")
    assert is_excluded(p) is None


def test_drops_years_just_over_threshold():
    p = mk("Software Engineer", "5 years of experience required.")
    reason = is_excluded(p)
    assert reason is not None
    assert "5+ years" in reason


def test_no_stem_opt_is_flagged_not_rejected():
    """Cap-exempt H-1B needs no E-Verify, so this must never be a hard stop."""
    p = annotate(mk("Data Analyst",
                    "This department does not participate in E-Verify."))
    assert p.sponsorship_flag == "no_stem_opt"
    assert p.hard_blockers == []
    assert is_excluded(p) is None


def test_drops_postdoc_titles():
    """Postdoc requires a completed PhD -- categorical mismatch for a new grad."""
    for t in ["Post-Doc Research Associate", "Postdoctoral Research Associate",
              "Postdoctoral Fellow in Biostatistics and Health Data Science",
              "Postdoctoral Research Scholar"]:
        reason = is_excluded(mk(t))
        assert reason is not None, t
        assert "post" in reason.lower(), t


def test_dedup():
    a = mk("Software Engineer")
    b = mk("Software Engineer")           # identical institution + job_id
    kept, stats = apply_filters([a, b])
    assert len(kept) == 1
    assert stats["duplicate"] == 1
