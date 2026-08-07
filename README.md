# uni-job-collector

Aggregates open tech postings across US university job boards into a single
ranked Excel sheet.

Universities are **H-1B cap-exempt** — they can petition year-round with no
lottery — which makes higher ed the highest-leverage channel for an F-1 new
grad. The problem is that ~130 relevant institutions are spread across four
different applicant-tracking platforms, and none of them call the job
"Software Engineer". This collects all of them, filters to what's actually
relevant, scores each posting against a skill profile, and writes one sheet.

## Status

| Adapter | Status | Institutions |
|---|---|---|
| Workday | **working, verified live** | ~75 (MinnState 33, UW 12, Penn State 24, OSU, UMD, WSU, LSU, USNH) |
| PeopleAdmin | **working, verified live** | ~7 (UNL, UNC, Utah, NC State, NDSU, IU, NU system) |
| PageUp | stub — 30-min DevTools recipe in the file | 4 (Oregon, MSU, Rutgers, Virginia Tech) |
| UMN PeopleSoft | stub — two documented routes | 5 U of M campuses |

Two adapters cover the large majority of reachable postings. The two stubs are
documented well enough to finish in an afternoon.

## Setup

```bash
cd uni-job-collector
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                                  # 46 tests, all offline
```

## Run

```bash
python run.py                          # everything enabled in targets.yaml
python run.py --only workday           # one platform
python run.py --name Wisconsin         # substring match on target name
python run.py --limit 2 --delay 2      # gentle smoke test
python run.py --from-cache             # re-filter/re-score, no network
python run.py --top 50                 # write only the 50 best rows
python run.py --keep 10                # keep 10 workbooks in output/ instead of 5
python run.py --keep 0                 # never prune old workbooks
```

Every run writes the workbook automatically as its last step — there's no
separate conversion command to remember. After writing, `run.py` prunes
`output/` down to the 5 most recent `workbook_*.xlsx` files (change with
`--keep`); `data/postings.json` and `data/history.json` are never touched by
pruning.

Outputs:

- `data/postings.json` — **this run only**, overwritten every time you collect.
  Handoff file for the Cowork side (`/job-fit` reads it), and what `--from-cache`
  re-reads to re-filter/re-score without hitting the network.
- `data/history.json` — **every posting ever collected, across every run**,
  deduplicated by institution + job ID. Never overwritten, never pruned.
  This is what `postings.json` can't give you: `postings.json` only knows
  about the postings that happened to still be live during your most recent
  collection, so if you didn't run for two weeks and a posting closed in
  between, it's just gone from that file. `history.json` is how the pipeline
  remembers a posting existed at all — it tracks `first_seen`, `last_seen`,
  `runs_seen`, and `status` (`new` / `open` / `closed`) per posting, which is
  what feeds the History and Changes tabs in the workbook.
- `output/workbook_<date>_<time>.xlsx` — **one workbook per run, six tabs**
  (built by `src/export/workbook.py`):

| Tab | Contents |
|---|---|
| Shortlist | this run, filtered + scored, best first |
| All Postings | this run, unfiltered |
| History | every posting ever seen, `open` or `closed`, deduped |
| Changes | new and closed since the previous run |
| Summary | counts by institution / portal / state / sponsorship |
| Run Stats | what the filters dropped and why |

Runs never overwrite each other — the filename carries the timestamp. Old
postings are not lost when a portal takes them down; they stay in History
marked `closed`, with `first_seen`, `last_seen` and `runs_seen` intact.

#### Column reference (Shortlist / All Postings / History / Changes)

All four data tabs share the same column vocabulary — a column just may not
apply to every tab (e.g. `Change` only appears on the Changes tab). Column
labels and widths live in `src/export/style.py::HEADERS`.

| Column | Meaning |
|---|---|
| Change | Changes tab only — `new` or `closed` this run |
| Status | History tab only — `open`, `closed`, or `new`; lifecycle state of the posting |
| Score | resume-match score from `src/score.py`, higher = better fit. Negative means a hard blocker or a sponsorship penalty outweighed everything else |
| Institution | university/system name |
| Title | job title exactly as posted |
| Department | hiring department or unit, if the portal exposes one |
| Location | campus or city, if provided |
| State | two-letter state, derived from the institution |
| Job ID | the portal's own requisition/job ID |
| Posted | date the posting first appeared, if the portal provides one |
| Closes | application deadline, if provided |
| First seen | History tab only — date this posting first showed up in any run |
| Last seen | History tab only — most recent run that still saw this posting live |
| Runs seen | History tab only — how many collection runs have included it |
| Sponsorship | `h1b_possible` (green) / `no_stem_opt` (amber — still applicable via cap-exempt H-1B) / `no_sponsorship_any` (red) / `unknown` |
| Sponsorship evidence | the sentence in the posting text that triggered the sponsorship flag |
| Blockers | hard disqualifiers detected — US citizenship, security clearance, export control. Red fill; usually means skip |
| Portal | ATS platform the posting came from — `workday` / `peopleadmin` / `pageup` / `custom` |
| System | multi-campus system, e.g. "Minnesota State", "Big Ten" |
| URL | direct link to the posting (clickable in Excel) |
| Why this score | full point-by-point breakdown behind the Score column, e.g. `Python (languages) +1.5; title match +3.0` |
| Description | full job description text — dropped from these four tabs (too wide); only appears in the raw export below |

### Raw export (`json_to_excel.py`)

`run.py` writes only postings that survive the filters and scores them. When
you need the unfiltered, unscored data itself — to eyeball a JSON file, hand
someone the raw feed, or work out why a posting never reached the Shortlist —
use `json_to_excel.py` instead:

```bash
python json_to_excel.py                      # data/postings.json -> output/
python json_to_excel.py --all                # every .json in data/, one tab each
python json_to_excel.py --sort score --desc  # sort by any column, descending
python json_to_excel.py path/to/other.json -o report.xlsx
python json_to_excel.py --no-summary         # skip the Summary tab
```

**Tabs it produces:**

| Tab | Contents |
|---|---|
| `<filename>` (one per input file, e.g. `postings`) | every row in that JSON file, no filtering, no scoring |
| Summary | counts by `institution`, `platform`, `state`, `sponsorship_flag`, `status` — across all converted files combined. Skipped with `--no-summary` |

**Columns:** whatever keys exist in the source JSON — this script handles
arbitrary JSON, not just this project's output, so it makes no assumptions
about schema. When pointed at `data/postings.json` or `data/history.json`,
that means every field on `Posting` (see the column reference table above),
**including `description`**, since the raw export applies no column drop list.
Known columns (`score`, `institution`, `sponsorship_flag`, etc.) get the same
friendly headers, widths, and color coding as the main workbook; unrecognized
keys become plain columns, alphabetized after the known ones.

It accepts a dict-wrapped list too (`{"postings": [...]}`), and `history.json`'s
id-keyed object shape (`{"<key>": {...}, ...}`) — `load_rows()` unwraps both.

`--from-cache` on `run.py` is the one to remember for iterating on scoring —
tuning `PROFILE` in `src/score.py` or the filter rules takes one second to
re-evaluate; you never need to re-hit the network to change how postings are
ranked.

## How it works

```
targets.yaml ──> adapters ──> Posting[] ──> filters ──> score ──> Excel
                                  │
                                  └──> data/postings.json (Cowork handoff)
```

Every adapter returns `List[Posting]`; nothing downstream knows which platform
a posting came from. **Adding a school is a config change, not a code change** —
append an entry to `targets.yaml`.

## Design notes worth knowing

- **Workday's `limit` is capped at 20.** Sending `limit=100` returns an empty
  list — not an error, not a truncated page. Silent failure. Don't "optimise"
  the page size.
- **`bulletFields` has no stable schema.** MinnState returns
  `[job_id, close_date, institution]`; Wisconsin returns
  `["Application Deadline: 08/02/2026"]`. Job IDs are parsed from
  `externalPath` instead, which is consistent everywhere. `_scan_bullets()`
  sniffs the rest rather than indexing positionally.
- **A "we don't do E-Verify / STEM OPT" line is flagged, never rejected.**
  E-Verify enrollment and H-1B cap-exemption are unrelated: the 24-month STEM
  OPT *extension* needs an E-Verify employer, but the initial 12-month OPT
  needs nothing from the employer, and a cap-exempt H-1B needs neither. A
  university that declines E-Verify can still hire on 12-month OPT and file
  cap-exempt at any point that year. Rejecting on that line would throw away
  most of the pipeline.
- **Hard blockers** (US citizenship, security clearance, export control) are
  real disqualifiers and are scored at −10. This is what keeps Penn State ARL,
  GTRI, JHU APL and Lincoln Lab roles out of the top of the list.
- **Scoring bias is acknowledged, not hidden.** Keyword overlap rewards jobs
  matching who you already are, so `STRETCH_SIGNALS` in `src/score.py`
  deliberately adds points for ML/HPC/RSE roles that are a reach.

## Layout

```
run.py                      pipeline entry point
json_to_excel.py            raw JSON -> Excel, no filtering
targets.yaml                institutions -> platform config
src/
  models.py                 Posting dataclass, dedup key, HTML stripping
  config.py                 targets.yaml loading + adapter dispatch
  filters.py                exclude/include rules, sponsorship + blocker detection
  score.py                  resume-match scoring + PROFILE (skills used for scoring, edit freely)
  adapters/
    base.py                 Adapter ABC, rate limiting, shared HTTP
    workday.py              working
    peopleadmin.py          working
    pageup.py               stub + DevTools recipe
    custom/umn.py           stub + two documented routes
  history.py                cumulative posting archive + run diffs
  export/
    style.py                shared Excel styling, one source of truth
    workbook.py             the six-tab workbook a run produces -- writes the .xlsx
    to_excel.py             deprecated stub, superseded by workbook.py. Kept only so
                            an old import fails loudly instead of writing the wrong
                            file. Safe to delete.
tests/                      46 tests, fixtures captured from live APIs
data/
  postings.json              LATEST run only, overwritten every time. This run's raw
                             collection, unfiltered. Cowork/job-fit handoff file.
  history.json               EVERY run, ever, deduped by institution + job ID. Never
                             overwritten, never pruned. See "What's history.json for?"
                             below.
output/
  workbook_<date>_<time>.xlsx  one per run, six tabs. Pruned to the 5 most recent
                               by run.py (change with --keep; see Run above).
```

## Next

1. Verify the 6 unverified Workday tenants in `targets.yaml` (open each careers
   URL, read tenant/site from the address bar, confirm non-zero `total`).
2. Finish the PageUp adapter — recipe is in the file.
3. Finish UMN — route A (PeopleSoft fluid POST) or route B (Playwright).
4. Schedule a weekly run and diff against the previous `postings.json` to get
   new-postings-only alerts.
