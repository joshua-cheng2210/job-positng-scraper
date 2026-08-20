# Working in this repo

Job-posting aggregator for university career sites. Read `README.md` first,
then `SPEC.md` for the verified API contracts.

## Ground rules

- **Adapters return `List[Posting]` and nothing else.** If you find yourself
  adding platform-specific branches downstream of an adapter, the abstraction
  has leaked — push the special case back into the adapter.
- **Adding an institution is a `targets.yaml` edit.** If it needs code, the
  adapter is wrong.
- **Never rely on Workday `bulletFields` positionally.** It is tenant-configured.
  See the module docstring in `src/adapters/workday.py`.
- **Workday `limit` max is 20.** Larger values return zero postings silently.
- **Do not reject postings for "no E-Verify" or "no STEM OPT."** Flag them.
  Cap-exempt H-1B does not require E-Verify. Rejecting on that line deletes
  most of the pipeline. `test_no_stem_opt_is_flagged_not_rejected` guards this.
- **Never prune `data/history.json`.** A posting that disappears from a portal
  is marked `closed`, not deleted. `test_missing_posting_is_closed_not_deleted`
  guards this. The whole point of the archive is that nothing is lost.
- **All Excel styling lives in `src/export/style.py`.** Two writers use it
  (`workbook.py` and `json_to_excel.py`); don't fork the constants.
- **Be polite to .edu servers.** `--delay` defaults to 1s between requests and
  `Adapter._sleep()` is called in every pagination loop. Don't remove it.

## Before committing

```bash
python -m pytest -q        # must stay green; 42 tests, all offline
python run.py --from-cache # must still produce an xlsx
python json_to_excel.py    # raw converter must still work
```

## Adding a new platform adapter

1. Subclass `Adapter` in `src/adapters/`, set `platform`, implement `fetch()`.
2. Register it in `REGISTRY` in `src/config.py`.
3. Capture a real API response into `tests/fixtures/`.
4. Add a parsing test that monkeypatches `_get` / `_post` — no network in tests.

## Things that will waste your time

- PeopleAdmin Atom feeds carry the full job description in `<content>`. Don't
  build a per-posting detail fetcher for it.
- Workday detail fetches cost one request each. Only enable `fetch_detail` for
  postings that already survived the title filter.
