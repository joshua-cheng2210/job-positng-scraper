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
python -m pytest -q                                  # 103 tests, all offline
```

## Run

```bash
python run.py                          # everything enabled in targets.yaml
python run.py --only workday           # one platform
python run.py --name Wisconsin         # substring match on target name
python run.py --limit 2 --delay 2      # gentle smoke test
python run.py --from-cache             # re-filter/re-score, still enriches survivors (see below)
python run.py --top 50                 # write only the 50 best rows
python run.py --keep 10                # keep 10 workbooks in output/ instead of 5
python run.py --keep 0                 # never prune old workbooks
python run.py --no-enrich              # skip the post-filter detail fetch entirely
```

Every run writes the workbook automatically as its last step — there's no
separate conversion command to remember. After writing, `run.py` prunes
`output/` down to the 5 most recent `workbook_*.xlsx` files (change with
`--keep`); `data/postings.json` and `data/history.json` are never touched by
pruning.

To prune `output/` by hand, without doing a full collection run (e.g. you
lowered `--keep` after the fact and want the backlog trimmed now):

```bash
python prune_workbooks.py              # keep the 5 most recent, delete the rest
python prune_workbooks.py --keep 10    # keep 10 instead
python prune_workbooks.py --dry-run    # show what would be deleted, don't delete
```

Same `workbook.prune()` function `run.py` calls internally — one place owns
"which files survive."

### Enrichment — why some descriptions are trustworthy and some aren't

The bulk collection pass does **not** reliably give you a complete job
description, and this was silently wrong for a while before it got caught by
comparing scraped output against live posting pages:

- **Workday** returns none at all by default. The list endpoint (`POST
  .../jobs`) never includes a description; a second per-posting request is
  needed, and it's gated behind `fetch_detail: true` in `targets.yaml`, which
  no target sets. Confirmed empirically: 0 of 3,450 collected Workday
  postings had any description.
- **PeopleAdmin**'s Atom feed looked fine — 88% of postings had *some* text —
  but the `<content>` tag only carries the first field (Description of
  Work / Job Summary / Essential Job Duties). It does **not** carry Minimum
  Required Qualifications or Preferred Qualifications, which is exactly
  where "N years required" and skill requirements live. Confirmed by
  comparing scraped `postings.json` rows against the actual live pages for
  UNL, University of Utah, and NC State postings — all three were missing
  the qualifications sections entirely.

So after filtering (title match, seniority, years-of-experience) but before
scoring, `run.py` calls `enrich_survivors()`, which fetches one extra detail
request **per posting that survived filtering** — never the full raw
collection, so it stays bounded to roughly Shortlist size (typically a few
hundred, not a few thousand):

- `src/adapters/workday.py::enrich()` — hits the same `jobPostingInfo` detail
  endpoint the dormant `fetch_detail` path already knew how to call, just
  invoked afterward instead of during the bulk pass.
- `src/adapters/peopleadmin.py::enrich()` — fetches the posting's own HTML
  page and parses every `<th>/<td>` field row PeopleAdmin renders, not just
  the feed's excerpt.

`Posting.description_scraped` is `1` only when one of these actually
succeeded and found real content; `0` means the description is either
missing entirely or is the partial/unverified excerpt — **don't trust
years-of-experience or skill-keyword conclusions on a `0` row.** It's a
column in the workbook (see below) so you can see which rows to trust at a
glance. Skip enrichment entirely with `--no-enrich` if you want a faster,
network-light run and don't mind less reliable filtering/scoring.

Enrichment runs concurrently (`--enrich-workers`, default 10 requests at
once) and logs a progress line every 25 completions
(`enrich progress: n/total`), so a run with a few hundred survivors and a
slow host or two doesn't look hung between the "enriching N/M survivors"
line and the final summary — watch for those progress lines advancing. Use
`--enrich-limit N` to cap enrichment to the first N unverified survivors if
you want a faster, partial run instead of waiting out the whole batch.

### AI Scoring — a second, LLM-judged opinion on top of the deterministic score

`src/score.py`'s score is cheap, deterministic, and regex-based on purpose —
its whole job is to narrow thousands of postings down to a Shortlist an LLM
pass can afford to look at, not to be the final word on fit. **AI Score**
is that LLM pass: a 0-10 fit judgment (0 = don't recommend) plus a one-line
reason, generated by actually reading the description against Josh's resume
— judgment a regex can't approximate.

`run.py` never calls an LLM itself — there's no API key, no `anthropic`
dependency, nothing that costs money to run. Instead, a separate skill,
**`/ai-score-shortlist`** (run manually in Cowork or Claude Code, whichever
you're in — it's just file reads/reasoning/a file write, no
environment-specific dependency), does the judging:

1. Reads the latest `output/workbook_<date>_<time>.xlsx` and takes **every
   row in both the Shortlist and Part-Time & Temporary tabs** — no Score
   threshold. This used to be gated to `history.json` rows with score `> 0`;
   that gate is gone now that hard-blockered postings are excluded from both
   tabs upstream (see Filtering below), so a low/negative regex score no
   longer implies "not worth a human looking at" — it often just means a
   years-of-experience or sponsorship penalty knocked points off a posting
   that's otherwise a real title/skill match.
2. Skips anything already present in `data/ai_scores.json` — so re-running it
   only scores what's new since last time, never redoes work.
3. Joins against `data/postings.json` for description text, judges each
   posting 0-10 using a condensed version of `/job-fit`'s actual verdict
   logic (sponsorship hard-stop = automatic 0, same "resolve uncertainty
   toward applying" bias, same qualification-gap sizing), and writes results
   to `data/ai_scores.json` **incrementally, per batch** — so it's resumable
   if you stop it partway through a large batch. Part-Time & Temporary rows
   get the same rubric as Shortlist rows — no penalty just for being in that
   tab.

`data/ai_scores.json` is keyed by `institution|job_id` (same scheme
`history.json` uses — see `workbook.py::ai_score_key()`), so it survives
across every `run.py` execution even though the workbook itself is
regenerated fresh every time. Run `/ai-score-shortlist`, then
`python run.py --from-cache` to see the results in the workbook — `run.py`
just reads whatever's in that file (blank if nothing's been scored yet,
never treated as a 0) and merges it in as the AI Score / AI Score Reason
columns.

This does **not** replace `/job-fit`. `/job-fit` stays the deep,
one-posting-at-a-time analysis (live page fetch, company research, resume
tailoring) you run right before actually applying. AI Score is the cheap
bulk pre-screen that tells you which of the — possibly hundreds of —
Shortlist postings are worth a `/job-fit` pass at all.

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
- `data/ai_scores.json` — written by `/ai-score-shortlist`, not by `run.py`.
  Never overwritten wholesale, never pruned — new entries merge in, existing
  ones are never silently redone. See AI Scoring above.
- `output/workbook_<date>_<time>.xlsx` — **one workbook per run, eight tabs**
  (built by `src/export/workbook.py`):

| Tab | Contents |
|---|---|
| Shortlist | this run, filtered + scored, **full-time only**, sorted by AI Score (postings not yet AI-scored sink to the bottom). Hard-blockered postings (citizenship, clearance, export control, student-status) are excluded entirely, not just flagged — see Filtering below |
| Part-Time & Temporary | same filtering as Shortlist, but for postings tagged part-time or temporary in the posting text. Split out so they don't compete with full-time roles for ranking, not because they're worse |
| Institutions | Shortlist (full-time only) grouped by institution and ranked — see below |
| All Postings | this run, unfiltered |
| History | every posting ever seen, `open` or `closed`, deduped |
| Changes | new and closed since the previous run |
| Summary | counts by institution / portal / state / sponsorship — Shortlist breakdowns exclude negative-score postings, see below |
| Run Stats | what the filters dropped and why |

Runs never overwrite each other — the filename carries the timestamp. Old
postings are not lost when a portal takes them down; they stay in History
marked `closed`, with `first_seen`, `last_seen` and `runs_seen` intact.

#### Institutions — "which school do I apply to first"

Every run, `src/export/workbook.py::institution_rankings()` groups the
Shortlist by institution and computes:

```
composite = top3_avg_score × sqrt(min(postings, 5))
```

Top-3 average (not overall average) so one bad posting at a school with
several good ones doesn't drag it down. The `sqrt(min(postings, 5))` volume
bonus rewards a school with several solid postings over one lucky outlier,
but caps out at 5 — an 11th posting from the same school shouldn't count for
much more than a 6th.

| Column | Meaning |
|---|---|
| Postings | how many Shortlist postings from this institution |
| Max Score / Avg Score / Top-3 Avg | self-explanatory; Top-3 Avg drives the ranking |
| Verified % | share of this institution's postings with `Description Verified? = 1`. **Read this before trusting the ranking** — a school at 0% verified has scores built on unconfirmed data (see the Enrichment section above); don't prioritize it over a lower-scoring but fully-verified school until enrichment actually succeeds for it |
| Positive Sponsorship Count / No Sponsorship Count | how many of this institution's postings had explicit `h1b_possible` / `no_sponsorship_any` language |
| Composite Rank | the sort key — highest first |

This is computed fresh from the current run every time, no LLM pass needed —
open the tab and read top to bottom.

#### Summary — filtered vs. unfiltered breakdowns

The Summary tab mixes two different kinds of question, and only one of them
gets filtered:

- **"Shortlist by institution"**, **"Shortlist by sponsorship flag"**,
  **"Shortlist by portal"**, **"Shortlist by state"**, and **"Shortlist by
  AI Score"** answer "where's it worth applying?" — so all five exclude
  postings with a negative net score (hard blocker, no sponsorship, 5+ years
  required, etc.). A school with three postings that all net negative
  shouldn't show up here next to schools with real prospects. AI Score
  buckets by the exact 0-10 value, plus a `(not yet AI-scored)` bucket —
  watching that count shrink run over run is the signal that
  `/ai-score-shortlist` is keeping up with the Shortlist.
- **"All postings by portal"**, **"All postings by state"**, and **"History
  by status"** answer "is collection working?" — a data-completeness
  question, not an application-worthiness one — so these stay unfiltered.
  Filtering them by score would make it look like collection is broken when
  it's really just that a state's postings didn't score well.

The Shortlist-by-portal and Shortlist-by-state blocks are placed directly
above their All-Postings counterparts, so the two are easy to eyeball
together — e.g. "12 of these 40 Minnesota postings actually made the
Shortlist" is one glance up-and-down instead of hunting across the sheet.

#### Column reference (Shortlist / All Postings / History / Changes)

All four data tabs share the same column vocabulary — a column just may not
apply to every tab (e.g. `Change` only appears on the Changes tab). Column
labels and widths live in `src/export/style.py::HEADERS`.

Some columns are **hidden by default** rather than dropped — the data is
still there (Summary counts, filtering, etc. all still see it), Excel just
collapses the column so the sheet reads cleaner. Unhide any of them the
normal Excel way (select the columns on either side, right-click, Unhide).
Hidden: `Department`, `Sponsorship evidence`, `Blockers` (populated rarely
enough that they were mostly empty space in the default view). `State` used
to be folded into a combined `Location` column and hidden too — reverted
back to its own plain, visible column.

`URL` is a different case — it's **dropped entirely**, not hidden. `Title`
is hyperlinked straight to the posting instead, so a separate URL column was
pure redundancy. The underlying `url` value is still read from the row data
to build that hyperlink; it just never gets its own column in these four
tabs. (The raw export in `json_to_excel.py` below still shows `url` as a
plain column since it doesn't hyperlink anything — it's a full, unstyled
data dump.)

| Column | Meaning |
|---|---|
| Change | Changes tab only — `new` or `closed` this run |
| Status | History tab only — `open`, `closed`, or `new`; lifecycle state of the posting |
| Score | resume-match score from `src/score.py`, higher = better fit. Negative means a hard blocker or a sponsorship penalty outweighed everything else |
| AI Score (0-10) | LLM fit judgment from `/ai-score-shortlist` — see AI Scoring above. **Blank means not yet scored**, not zero; `0` is an actual judgment ("don't recommend"). Color-coded: red 0-2, amber 3-6, green 7-10 |
| AI Score Reason | one-line reason behind the AI Score — a real gap or match it found, not a generic comment |
| Institution | university/system name |
| Title | job title exactly as posted, **and hyperlinked straight to the application URL** — click the title to open the posting, no separate URL column needed |
| Department *(hidden)* | hiring department or unit, if the portal exposes one |
| Location | campus or city, if provided |
| State | two-letter state, its own plain column |
| Job ID | the portal's own requisition/job ID |
| Posted | date the posting first appeared, if the portal provides one |
| Days Since Posted | `(today − Posted)` in days, recomputed fresh every time the workbook is written — not stored, so it's always accurate as of when you open it. Blank if the portal never gave a posted date |
| Closes | application deadline, if provided |
| First seen | History tab only — date this posting first showed up in any run |
| Last seen | History tab only — most recent run that still saw this posting live |
| Runs seen | History tab only — how many collection runs have included it |
| Sponsorship | `h1b_possible` (green) / `no_stem_opt` (amber — still applicable via cap-exempt H-1B) / `no_sponsorship_any` (red) / `unknown` |
| Sponsorship evidence *(hidden)* | the sentence in the posting text that triggered the sponsorship flag |
| Blockers *(hidden)* | hard disqualifiers detected — US citizenship, security clearance, export control, or student status required (see `HARD_BLOCKERS` in `filters.py`). Red fill; usually means skip. On the Shortlist and Part-Time & Temporary tabs this column is always empty by construction — anything with a blocker is excluded from those two tabs before the workbook is written (see the tab table and Filtering section above). It still shows up on All Postings/History, where it's populated for defense-adjacent research roles (e.g. an applied research lab) and student-employee/work-study postings |
| Portal | ATS platform the posting came from — `workday` / `peopleadmin` / `pageup` / `custom` |
| System | multi-campus system, e.g. "Minnesota State", "Big Ten" |
| Why this score | full point-by-point breakdown behind the Score column, e.g. `Python (languages) +1.5; title match +3.0; posted <24h ago +3.0` |
| Description Verified? | `1` if `description` came from a real detail fetch (Workday `jobPostingInfo`, or a parsed PeopleAdmin HTML page) — trust years/skill conclusions on these rows. `0` means missing or only a partial feed excerpt — don't trust it. See "Enrichment" above. Green fill = `1` |
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

## Filtering (`src/filters.py`) — what gets removed entirely

`apply_filters()` runs once, before scoring, and drops postings for free —
no LLM, no network. For each posting it checks, in order, and stops at the
first hit:

| # | Check | Removes it if... |
|---|---|---|
| 1 | Duplicate | same `institution + job_id` already seen this run (dedup key) |
| 2 | `EXCLUDE_TITLE` | title matches a non-tech role — see categories below |
| 3 | `EXCLUDE_SENIORITY` | title signals too senior for a new grad — see below |
| 4 | Years of experience | title/description mentions more than `MAX_YEARS_EXPERIENCE` (4, in `filters.py`) years required — see note below |
| 5 | `INCLUDE_TITLE` | title matches **none** of the allowlisted tech-job patterns |

A posting that survives all five gets `annotate()`'d (sponsorship flag +
hard-blocker tags) and moves on to scoring.

**`EXCLUDE_TITLE`** — hard "not a tech role" categories: academic faculty
(`faculty`, `professor`, `lecturer`, `adjunct`, `dean`, `provost`), medical/
clinical (`nurse`, `physician`, `clinician`, `therapist`, `dental`,
`veterinary`), skilled trades (`electrician`, `plumber`, `hvac`, `welder`,
`mechanic`, `carpenter`), food service (`cook`, `chef`, `barista`, `dining`),
safety/facilities (`police`, `security officer`, `firefighter`, `custodian`,
`groundskeeper`, `parking`, `warehouse`), fundraising/admin (`development
officer`, `fundraising`, `advancement`, `admissions counselor`, `career
coach`, `academic advisor`), and `librarian`, `counselor`, `chaplain`,
`social work`, plus `post-doc`/`postdoc`/`postdoctoral` (a PhD is a
categorical mismatch for a new grad) and `undergrad`/`undergraduate` (the
opposite categorical mismatch — he graduates May 2026, so a title that says
the job is *for* an undergrad, e.g. "Undergraduate Research Assistant," is
closed to him regardless of skill fit).

**`EXCLUDE_SENIORITY`** — checked case-sensitively against the raw title
(so `IT` doesn't accidentally match roman numeral `I`): `senior`, `sr.`,
`staff engineer/scientist`, `lead`, `principal`, `manager`, `director`,
`head of`, `chief`, `supervisor`, `executive`, `vice president`, `associate
dean`, `architect`, and title-level roman numerals `III`/`IV`/`V`.

**Years of experience** — scans the *entire* posting text (title +
department + description) for the highest "N years" figure mentioned
anywhere, not just a dedicated field. This means a screening question like
"how many years of experience do you have? ... 15 years or more" counts too,
same as an explicit "requires 8 years." Threshold is 4; edit
`MAX_YEARS_EXPERIENCE` in `filters.py` to change it. This exists because
`score.py`'s years penalty alone wasn't enough — a posting can rack up
enough keyword/stretch points to still rank near the top even after a
penalty, so anything over the threshold is now removed outright rather than
just deprioritized.

**`INCLUDE_TITLE`** — the allowlist of what a university actually calls
Josh's kind of job: `software engineer/developer`, `research software`,
`application developer/programmer/analyst`, `web/full-stack/front-end/
back-end developer`, `data analyst/scientist/engineer`, `business
intelligence`, `systems analyst/administrator/engineer`, `database
administrator/analyst/developer`, `devops`, `cloud`, `platform engineer`,
`site reliability`, `IT specialist/analyst/support`, `research professional/
associate/assistant/technician/specialist`, `machine learning`, `artificial
intelligence`, `ai/ml engineer`, `qa`, `test engineer`, `instructional
design`, `institutional research`, `hpc`/`high-performance computing`, and a
few more niche patterns. A title matching **none** of these is dropped as
"title does not match any tech pattern" — this is the most common rejection
reason in `Run Stats`.

**What filtering deliberately does NOT remove (from `filters.py` itself):**

- **Hard blockers** (US citizenship required, security clearance, export
  control, student status required) are tagged by `annotate()` but the
  posting stays in `apply_filters()`'s output — it's a scoring penalty
  (`-10.0`), not a `filters.py` exclude. You'll still see it in All
  Postings/History with a red `Blockers` cell, just buried near the bottom
  by score. **The Shortlist and Part-Time & Temporary tabs are a separate
  story** — `run.py::split_shortlist()` drops anything with a hard blocker
  from both of those tabs entirely before the workbook is written (see the
  tab table above), so those two application-facing tabs are hard-blocker-
  free even though `filters.py` and `score.py` never learned to exclude
  them outright.
- **Sponsorship signals** (`no_sponsorship_any`, `no_stem_opt`,
  `h1b_possible`, `unknown`) are also tag-only, everywhere, including the
  Shortlist/Part-Time & Temporary tabs. In particular, a "we don't do
  E-Verify / STEM OPT" line is **never** a reason to drop a posting —
  cap-exempt H-1B needs no E-Verify, so rejecting on that line would throw
  away most of the pipeline. It's flagged amber for prioritization instead,
  and now costs a small `-2.0` in scoring (see below) rather than being
  score-neutral.

## Scoring (`src/score.py`) — what ranks the survivors

Only postings that passed every filter above get scored. `rank()` scores
every posting in the run (not just the kept ones — the History/All Postings
tabs need scores too) and sorts descending. Everything below is additive
unless marked as a penalty.

**Title tier** — only the single highest-matching tier applies, not all of
them:

| Tier | Bonus | Example title patterns |
|---|---|---|
| 1 | `+3.0` | `software engineer/developer`, `research software`, `data analyst/scientist/engineer`, `application developer` |
| 2 | `+2.0` | `programmer`, `web developer`, `business intelligence`, `systems analyst`, `computational`, `research professional` |
| 3 | `+1.0` | `IT specialist/analyst`, `database`, `qa`, `research associate/assistant/technician` |

**Keyword categories** — every match in `PROFILE` (`src/score.py`) adds
points; a posting can match many keywords across many categories:

| Category | Weight each | Examples |
|---|---|---|
| `languages` | `+1.5` | Python, Java, JavaScript, C++, SQL, HTML, CSS, OCaml, R |
| `frameworks` | `+1.0` | Node.js, React, Angular, Flask, Vite, Tailwind CSS, Django |
| `databases_tools` | `+1.0` | PostgreSQL, MySQL, Git, Docker, Jira, Tableau, Excel |
| `cloud_deployment` | `+1.0` | AWS, S3, CloudFront, Lambda, GitHub Pages, Render |
| `skills` (legacy) | `+1.0` | REST API, Linux |
| `data_analytics` | `+0.75` | NumPy, pandas, Matplotlib, Seaborn, OpenCV, Regex, ChromaDB |
| `nice_to_have` | `+0.5` | TypeScript, Power BI, MATLAB, Kubernetes, Azure, PyTorch |
| `design_productivity` | `+0.5` | Figma, JMP, TeamDynamix, TargetProcess |

**Stretch signals** (`STRETCH_SIGNALS`) — deliberately reward reach roles
Josh isn't already a strong match for, so the ranking doesn't just converge
on "what he already is":

| Signal | Bonus |
|---|---|
| `research software engineer` | `+2.5` |
| `machine learning` | `+2.0` |
| `artificial intelligence` | `+2.0` |
| `hpc` / `high-performance computing` | `+2.0` |
| `bioinformatics` | `+1.5` |

**Entry-level signals** (`ENTRY_SIGNALS`):

| Signal | Bonus |
|---|---|
| `new grad` | `+2.5` |
| `entry-level` | `+2.0` |
| `0-1`, `1-2`, or `1-3 years` | `+1.5` |
| `bachelor's degree` | `+1.0` |
| level marker "Analyst I" / "Developer 1" | `+0.5` |

**Recency bonus** — freshly-posted roles are worth applying to before the
applicant pool grows, so newer postings get a small push toward the top of
the Shortlist. Mutually exclusive — only the stronger bonus applies, and it
requires `posted_date` to be set at all (blank for postings the portal never
gave a date for, in which case no bonus applies either way):

| Signal | Bonus |
|---|---|
| Posted less than 24 hours ago | `+3.0` |
| Posted less than 3 days ago | `+2.0` |

Caveat worth knowing: every adapter only gives a `date`, never a timestamp,
so "less than 24 hours ago" is approximated as "posted today" (same
calendar date as the run) and "less than 3 days ago" as "posted 1–2 days
ago." Close enough for ranking purposes, but don't read it as a literal
hour count.

**Penalties:**

| Penalty | Amount | Note |
|---|---|---|
| Requires 5+ years | `-10.0` | Same regex idea as the filters.py hard exclude, but this is scoring-only, so it still applies even to postings that were removed from the Shortlist — it's how All Postings/History show a rock-bottom score for them |
| Requires 3–4 years | `-2.0` | Below the filters.py hard-exclude threshold, so these stay in the Shortlist just deprioritized |
| Hard blocker present | `-10.0` | US citizenship / clearance / export control — see filters.py above |
| `no_sponsorship_any` | `-3.0` | explicit "will not sponsor any visa" language |
| `h1b_possible` | `+2.0` | explicit positive sponsorship or cap-exempt language |
| `no_stem_opt` | `-2.0` | still **not** a hard exclude — cap-exempt H-1B needs no E-Verify/STEM OPT, so Josh still applies. Just a small negative signal since a plain STEM-OPT-friendly posting is a marginally safer bet |
| `part-time` | `-2.0` | matches "part-time" or "part time" anywhere in title/department/description. Not a filters.py exclude — some part-time roles are still worth applying to — just deprioritized |
| `temporary` | `-2.0` | matches "temporary" anywhere in title/department/description. Same reasoning: a real negative signal, not disqualifying (e.g. a temp role that regularly converts to full-time) |

Both employment-type penalties can stack with each other and with everything
else — a part-time, temporary, 5+ years posting nets `-14.0` from these three
penalties alone before any positive keyword matches offset it.

**Unscraped fairness bonus:**

| Bonus | Amount | Note |
|---|---|---|
| `description_scraped == 0` | `+3.0` | Compensates for a real bias, not a courtesy — see below |

`haystack` (what every keyword-category match, stretch/entry signal,
years-of-experience check, and sponsorship/blocker scan runs against) is
`title + department + description`. When `description` is missing or
unverified, none of those checks find anything — not just no bonus, no
penalty either. An unscraped posting gets squeezed into a narrow band near
its title-tier score alone, while scraped postings spread out both above
*and below* it. Without this offset, a genuinely great-fit posting that
simply hasn't been enriched yet (enrichment is bounded to Shortlist
survivors and isn't guaranteed to succeed for all of them — see
Enrichment above) can rank below a mediocre *scraped* posting purely for
lack of text. That's a missed opportunity, not a reflection of actual fit,
so it gets a flat `+3.0` rather than being left to silently sink.

This does **not** mean the posting is a good match — `description_scraped`
stays `0` and the workbook still flags it unverified. It only stops the
ranking from treating "we don't know yet" the same as "we checked, and it's
average." `+3.0` is a judgment call (matches the recency bonus's scale),
not a measured average — tune `UNSCRAPED_BENEFIT_OF_DOUBT` in `score.py`
if it turns out to over- or under-correct in practice.

## Design notes worth knowing

- **Workday's `limit` is capped at 20.** Sending `limit=100` returns an empty
  list — not an error, not a truncated page. Silent failure. Don't "optimise"
  the page size.
- **`bulletFields` has no stable schema.** MinnState returns
  `[job_id, close_date, institution]`; Wisconsin returns
  `["Application Deadline: 08/02/2026"]`. Job IDs are parsed from
  `externalPath` instead, which is consistent everywhere. `_scan_bullets()`
  sniffs the rest rather than indexing positionally.
- **A "we don't do E-Verify / STEM OPT" line is flagged, never rejected —**
  but it does now cost `-2.0` in scoring. E-Verify enrollment and H-1B
  cap-exemption are unrelated: the 24-month STEM OPT *extension* needs an
  E-Verify employer, but the initial 12-month OPT needs nothing from the
  employer, and a cap-exempt H-1B needs neither. A university that declines
  E-Verify can still hire on 12-month OPT and file cap-exempt at any point
  that year. Rejecting on that line outright would throw away most of the
  pipeline — the small penalty just nudges it below an otherwise-equal
  posting with no such caveat, rather than being fully neutral.
- **Hard blockers** (US citizenship, security clearance, export control,
  student status required) are real disqualifiers and are scored at −10 by
  `score.py`. On top of that, `run.py::split_shortlist()` now drops any
  hard-blockered posting from the Shortlist and Part-Time & Temporary tabs
  entirely — not just to the bottom of the ranking. This is what keeps Penn
  State ARL, GTRI, JHU APL, Lincoln Lab, and student-employee/work-study
  roles out of the two tabs a human actually applies from.
- **The Part-Time & Temporary split reuses `score.py`'s employment-type
  regex, with one fix.** Generic benefits-eligibility boilerplate some
  portals include on every posting ("Perks and Benefit eligibility is based
  on Part-Time or Full-Time Employment status") matches `\bpart[- ]time\b`
  without describing *that specific posting's* employment type. Fine as a
  soft `-2.0` score nudge; not fine as a hard tab-routing decision — it was
  silently moving genuinely full-time postings (e.g. a Business Intelligence
  Analyst role) into the Part-Time & Temporary tab. `run.py::_is_temp_or_parttime()`
  strips that boilerplate phrase before matching; `score.py`'s scoring
  penalty is untouched since a soft nudge tolerates the false positive fine.
- **A posting requiring more than `MAX_YEARS_EXPERIENCE` (4) years is a hard
  exclude, not just a scoring penalty.** `score.py` docks points for "N years
  required," but a penalty alone doesn't stop a posting with heavy
  keyword/stretch overlap from still floating to the top — a University of
  Utah "AI/ML Engineer" posting requiring 6–8 years scored `4.0` net despite
  the (then) `-4.0` penalty, because `+4.0` in stretch signals and skill
  matches outweighed it. The 5+ years penalty was later raised to `-10.0`
  specifically to make cases like this net-negative on their own, but the
  hard exclude in `filters.py::_years_required()` still removes it from the
  Shortlist outright before scoring ever sees it — belt and suspenders.
  Threshold and regex live at the top of the "experience" section in
  `filters.py` — edit
  `MAX_YEARS_EXPERIENCE` there if 4 is too strict or too loose.
- **Scoring bias is acknowledged, not hidden.** Keyword overlap rewards jobs
  matching who you already are, so `STRETCH_SIGNALS` in `src/score.py`
  deliberately adds points for ML/HPC/RSE roles that are a reach.

## Layout

```
run.py                      pipeline entry point -- split_shortlist() separates the
                            filtered/scored survivors into Shortlist (full-time,
                            hard-blocker-free) and Part-Time & Temporary
json_to_excel.py            raw JSON -> Excel, no filtering
prune_workbooks.py          manual output/ cleanup, same logic run.py uses automatically
targets.yaml                institutions -> platform config
src/
  models.py                 Posting dataclass, dedup key, HTML stripping
  config.py                 targets.yaml loading + adapter dispatch
  filters.py                exclude/include rules, sponsorship + blocker detection
                            (HARD_BLOCKERS: citizenship, clearance, export control,
                            student status; EXCLUDE_TITLE also drops undergrad-only
                            titles now)
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
    workbook.py             the eight-tab workbook a run produces -- writes the .xlsx,
                            including the Part-Time & Temporary tab
    to_excel.py             deprecated stub, superseded by workbook.py. Kept only so
                            an old import fails loudly instead of writing the wrong
                            file. Safe to delete.
tests/                      103 tests, fixtures captured from live APIs
data/
  postings.json              LATEST run only, overwritten every time. This run's raw
                             collection, unfiltered. Cowork/job-fit handoff file.
  history.json               EVERY run, ever, deduped by institution + job ID. Never
                             overwritten, never pruned. See "What's history.json for?"
                             below.
  ai_scores.json              written by /ai-score-shortlist, never by run.py. Keyed
                             institution|job_id; merged into the workbook's AI Score /
                             AI Score Reason columns on every run.
output/
  workbook_<date>_<time>.xlsx  one per run, eight tabs. Pruned to the 5 most recent
                               by run.py (change with --keep; see Run above).
```

## Next

1. Verify the 6 unverified Workday tenants in `targets.yaml` (open each careers
   URL, read tenant/site from the address bar, confirm non-zero `total`).
2. Finish the PageUp adapter — recipe is in the file.
3. Finish UMN — route A (PeopleSoft fluid POST) or route B (Playwright).
4. Schedule a weekly run and diff against the previous `postings.json` to get
   new-postings-only alerts.
