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
python -m pytest -q                                  # 42 tests, all offline
```

## Run

```bash
python run.py                          # everything enabled in targets.yaml
python run.py --only workday           # one platform
python run.py --name Wisconsin         # substring match on target name
python run.py --limit 2 --delay 2      # gentle smoke test
python run.py --from-cache             # re-filter/re-score, no network
python run.py --top 50                 # write only the 50 best rows
```

Outputs:

- `data/postings.json` — this run's raw collection. Handoff file for the Cowork
  side (`/job-fit` reads it).
- `data/history.json` — every posting ever collected, deduplicated by
  institution + job ID. Never overwritten, never pruned.
- `output/workbook_<date>_<time>.xlsx` — **one workbook per run, six tabs**:

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

### Raw export

`run.py` writes only postings that survive the filters. To dump everything in a
JSON file with no filtering and no scoring:

```bash
python json_to_excel.py                      # data/postings.json -> output/
python json_to_excel.py --all                # every .json in data/, one sheet each
python json_to_excel.py --sort score --desc  # sort by any column
python json_to_excel.py path/to/other.json -o report.xlsx
```

Useful for eyeballing the full collection or working out why a posting never
reached the filtered sheet. It also handles arbitrary JSON — unknown keys become
columns — so it works on data that didn't come from this project.

`--from-cache` is the one to remember. Tuning `profile.yaml` or the filter
rules takes one second to re-evaluate; you never need to re-hit the network to
change how postings are ranked.

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
profile.yaml                skills used for scoring (edit freely)
src/
  models.py                 Posting dataclass, dedup key, HTML stripping
  config.py                 targets.yaml loading + adapter dispatch
  filters.py                exclude/include rules, sponsorship + blocker detection
  score.py                  resume-match scoring
  adapters/
    base.py                 Adapter ABC, rate limiting, shared HTTP
    workday.py              working
    peopleadmin.py          working
    pageup.py               stub + DevTools recipe
    custom/umn.py           stub + two documented routes
  history.py                cumulative posting archive + run diffs
  export/
    style.py                shared Excel styling, one source of truth
    workbook.py             the six-tab workbook a run produces
tests/                      42 tests, fixtures captured from live APIs
```

## Next

1. Verify the 6 unverified Workday tenants in `targets.yaml` (open each careers
   URL, read tenant/site from the address bar, confirm non-zero `total`).
2. Finish the PageUp adapter — recipe is in the file.
3. Finish UMN — route A (PeopleSoft fluid POST) or route B (Playwright).
4. Schedule a weekly run and diff against the previous `postings.json` to get
   new-postings-only alerts.
