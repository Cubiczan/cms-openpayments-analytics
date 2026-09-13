# cms-openpayments-analytics

A dbt project over DuckDB that turns **CMS Open Payments — 49M rows, 29 GB of source CSV —
into tested analytical marts on a laptop, in minutes, for $0.**

No cloud warehouse. No Spark. No credit card.

## What it does

CMS publishes every payment US drug and device manufacturers make to physicians and teaching
hospitals. It is one of the richest open datasets in healthcare and almost nobody queries it
properly, because 29 GB of CSV is awkward enough that most analysis stops at a pivot table.

This project:

1. Converts the raw CSVs to **718 MB of ZSTD Parquet** (40x smaller, ~20 minutes, once)
2. Builds staging and mart models with dbt
3. Runs a test suite that encodes real analytical failures, not just schema checks

Typical query latency against 46M rows: **0.1–0.6 seconds**.

## Why tests matter more than speed

Speed is the easy part. The hard part is that analytical bugs are *silent* — they produce a
plausible number, which gets into a deck, and is discovered weeks later by someone else.

Every test in `tests/` encodes a mistake that actually shipped:

| Test | The failure it prevents |
|---|---|
| `assert_manufacturer_names_collapsed` | Manufacturer names are **not normalised** in CMS: 2,472 written names resolve to **2,269 real companies**. AbbVie appears under two spellings. Grouping by the raw name understates the biggest sponsors by roughly half — and reads as authoritative. |
| `assert_no_irb_in_site_ranking` | Central IRBs (Advarra, WCG) appear in the data as research **sites**. Advarra shows up with **117 principal investigators** and will top any unfiltered site ranking. |
| `assert_no_implausible_site_size` | A generalised version of the above, to catch the *next* aggregator. Threshold calibrated against reality: it first used >60 and failed on Massachusetts General (64 PIs) and Cleveland Clinic (61), which are genuine. The threshold was wrong, not the data. |
| `assert_manufacturers_not_truncated` | CRM systems commonly store only a contact's *top* manufacturers. That makes mid-tier companies invisible — a TMS query returned zero for Neuronetics despite $2.8M in source, and LivaNova, the largest psychiatry spender at **$55.2M**, never appeared at all. |
| `assert_bench_exceeds_active` | A **business-assumption monitor**, not a data check. It fails if a premise the analysis depends on stops holding. |

That last one caught a genuine error on its first run: an earlier analysis compared one
population against a different one and reached the opposite conclusion. The number had already
been published.

## Quick start

```bash
pip install dbt-core dbt-duckdb

# download the CMS Open Payments detail files (general + research, PY2023-2025)
#   https://openpaymentsdata.cms.gov/datasets
python scripts/to_parquet.py          # 29 GB CSV -> 718 MB Parquet, once

dbt deps --profiles-dir .
dbt build --profiles-dir .            # builds every model AND runs every test
```

Then query it from anything:

```python
import duckdb
con = duckdb.connect("data/openpayments.duckdb", read_only=True)
con.execute("select * from mart_sponsor_ranking limit 10").fetchdf()
```

> Open analysis connections with `read_only=True`. DuckDB allows **one writer**, and a
> notebook holding a write connection makes `dbt build` fail with a file-lock error whose
> message does not point at the cause.

## Models

| Model | Grain | Purpose |
|---|---|---|
| `stg_manufacturers` | company | One row per *real* company; collapses spelling variants |
| `stg_research_payments` | payment | Research payments, site resolved, non-sites flagged not dropped |
| `stg_research_investigators` | payment × PI | Unpivots all **five** principal-investigator slots |
| `mart_sponsor_ranking` | company | Sponsor totals, correctly grouped |
| `mart_bh_research_sites` | site | Behavioural-health research sites, IRBs excluded |
| `mart_bh_investigators` | NPI | Named principal investigators — trial evidence |
| `mart_npi_manufacturers` | NPI × company | Full manufacturer relationships, untruncated |
| `mart_psychiatry_bench` | NPI | Active investigators vs untapped capacity |
| `mart_marketing_audience` | NPI | Identity + intent + evidence per clinician |

## Two conventions that are not optional

**Never group by a raw company or site name.** Use `{{ normalize_entity('col') }}`. See the
2,472 → 2,269 problem above.

**Cast source columns explicitly in staging.** DuckDB infers Parquet types per file and the
CMS program years disagree with each other: principal-investigator NPIs arrive as `BIGINT` in
slots 1–2 and `VARCHAR` in slots 3–5. Comparing a BIGINT to `''` throws at runtime.

## Performance note

`mart_npi_manufacturers` takes ~16 minutes on 46M rows. The first version was far worse and
was killed twice: it called `normalize_entity()` inline, running two regexes per row across
46M rows. There are only ~2,472 distinct manufacturer names in the entire corpus, so the
normalisation belongs in a dimension that is joined once — not an expression evaluated per
row. Same output, no runaway. Making it incremental on `Program_Year` is the remaining fix.

## Scope

Public CMS and commercial data only. **No patient-level data belongs in this project** — the
CMS files contain none, and it should stay that way if you extend it.

## Licence

MIT.
