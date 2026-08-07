# API contracts

Everything here was captured from the live services on **2026-07-28** by
issuing the requests from the browser and reading the responses. Nothing in
this document is inferred from documentation.

---

## 1. Workday CXS — VERIFIED

Used by MinnState, Universities of Wisconsin, Penn State, Ohio State, UMD,
WSU, LSU, USNH and most large public systems.

### List

```
POST https://{host}/wday/cxs/{tenant}/{site}/jobs
Content-Type: application/json

{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
```

Response:

```json
{
  "total": 184,
  "jobPostings": [
    {
      "title": "Coordinator for Allied Health Placements - MSUAASF Range C",
      "externalPath": "/job/St-Cloud/Coordinator-for-Allied-Health-Placements_JR0000005305",
      "locationsText": "St. Cloud",
      "postedOn": "Posted Today",
      "bulletFields": ["JR0000005305", "2026-08-11", "St. Cloud State University"]
    }
  ],
  "facets": [{"facetParameter": "Institution", "descriptor": "Institution", "values": [...]}],
  "userAuthenticated": false
}
```

### Detail

```
GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}
```

```json
{"jobPostingInfo": {
  "id": "6a93f445f48a1001b8c452b6c0c70000",
  "title": "...", "jobDescription": "<full HTML>", "location": "St. Cloud",
  "startDate": "2026-07-28", "endDate": "2026-08-11", "timeType": "Full time",
  "jobReqId": "JR0000005305", "externalUrl": "https://...", "canApply": true
}}
```

### Two traps

**`limit` is capped at 20.** Measured directly:

| limit sent | postings returned |
|---|---|
| 20 | 20 |
| 100 | **0** |

Not an error, not a truncation — an empty list. Any "optimisation" past 20
silently collects nothing.

**`bulletFields` is tenant-configured and has no fixed schema:**

| Tenant | bulletFields |
|---|---|
| minnstate | `["JR0000005305", "2026-08-11", "St. Cloud State University"]` |
| wisconsin | `["Application Deadline: 08/02/2026"]` |

Never index it. `_scan_bullets()` sniffs each element (is it a date? is it a
wordy non-id string?) and returns `(close_date, institution_hint)`.

Job IDs come from `externalPath`, which was consistent across every tenant
observed. Three shapes must survive:

```
.../Network-Engineer_JR0000005315   -> JR0000005315
.../App-Dev_JR10012777              -> JR10012777
.../Event-Intern_REQ_0000062018-1   -> REQ_0000062018-1   (the id contains _)
```

The third is why the regex carries an optional `(?:[A-Za-z]+_)?` prefix group.
Without it the match begins at the *last* underscore and silently returns
`0000062018-1`. A unit test pins all three.

### Observed tenants

| Institution | host | tenant | site | total |
|---|---|---|---|---|
| Minnesota State | minnstate.wd115.myworkdayjobs.com | minnstate | Minnesota_State_Careers | 184 |
| Universities of Wisconsin | wisconsin.wd1.myworkdayjobs.com | wisconsin | UW_Comprehensives | 354 |

The other six in `targets.yaml` are marked `verified: false`. To verify: open
the careers URL, read `{tenant}` and `{site}` off the address bar, POST the
list endpoint, confirm `total > 0`.

---

## 2. PeopleAdmin Atom — VERIFIED

Used by UNL, UNC-Chapel Hill, University of Utah, NC State, NDSU, IU academic.

```
GET {base_url}/postings/search.atom
```

```xml
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://employment.unl.edu/postings/101479</id>
    <published>2026-07-28T16:34:58-05:00</published>
    <updated>2026-07-28T16:34:59-05:00</updated>
    <link rel="alternate" type="text/html" href="https://employment.unl.edu/postings/101479"/>
    <title>Extension Instructor or Open Rank Extension Educator</title>
    <content>&lt;div&gt;...ENTIRE job description as escaped HTML...&lt;/div&gt;</content>
    <author><name>NE Ext Engagement Zone 1-12350</name></author>
  </entry>
</feed>
```

**The whole feed is one request and `<content>` carries the full description**,
so sponsorship scanning works with no per-posting fetch. This is the cheapest
adapter in the project.

- `job_id` — trailing integer of `<id>` (`/postings/(\d+)`)
- `department` — `<author><name>` with the trailing `-1234` unit code stripped
- **No location field.** Falls back to `default_location` in `targets.yaml`,
  which for a single-campus portal is correct anyway.

Real sponsorship language observed in this feed, which is exactly what the
flagger is tuned against:

> This position is not eligible for employment-based permanent residency
> sponsorship. UNL may be able to sponsor temporary work authorization
> (e.g., H-1B) for the successful candidate.

---

## 3. PageUp — NOT VERIFIED

Sites live at `{host}/cw/en-us/listing/`. The JSON search endpoint varies by
tenant and PageUp version. Recipe to finish, in `src/adapters/pageup.py`:

1. Open `https://careers.uoregon.edu/cw/en-us/listing/`
2. DevTools → Network → XHR → page through the listings
3. Look for `/cw/en-us/search` (POST JSON), `/cw/en-us/listing/?page=N`
   (HTML fragment), or `/search/?q=&startrow=0` (JSON)
4. Copy as cURL, confirm the shape, fill in `_search_url` / `_parse`

Prefer JSON. HTML scraping breaks on every redesign.

---

## 4. UMN PeopleSoft — NOT VERIFIED

`https://hr.myu.umn.edu/jobs/ext/` redirects to a PeopleSoft fluid page:

```
https://hr.myu.umn.edu/psc/hrprd/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL
```

Stateful, ICSID/ICStateNum tokens, no public JSON API. Two routes documented in
`src/adapters/custom/umn.py`:

- **A — replay the fluid POST** (~1h): scrape `ICSID`/`ICStateNum` from hidden
  inputs, POST with a `requests.Session()`, parse the HTML fragment.
- **B — Playwright** (~30min): render the page, read the result rows. Slower
  and more fragile but works today.

Observed 2026-07-27: 702–715 open postings; location filter values are Twin
Cities, Duluth, Morris, Crookston, Rochester; ~9 postings in the Information
Technology job family and ~55 in Research.

---

## 5. Data contract

`Posting` (see `src/models.py`) — the only type crossing the adapter boundary:

| Field | Notes |
|---|---|
| `institution`, `job_id`, `title`, `url`, `platform` | required |
| `department`, `location`, `posted_date`, `close_date`, `description` | best effort |
| `system`, `state` | from `targets.yaml` |
| `key` | sha1 of `institution|job_id`, used for dedup |
| `score`, `score_reasons` | filled by `src/score.py` |
| `sponsorship_flag` | `h1b_possible` / `no_stem_opt` / `no_sponsorship_any` / `unknown` |
| `hard_blockers` | citizenship / clearance / export control |

`data/postings.json` is a list of `Posting.to_row()` dicts. That file is the
contract with the Cowork side — `/job-fit` reads it, ranks, and runs the deep
analysis on the top N.
