-- Every manufacturer relationship for clinicians in our addressable audience, untruncated.
--
-- Fixes a defect that cost us a real answer: the the CRM property
-- openpayments_manufacturers stored only each contact's top manufacturers, so mid-tier
-- companies were invisible. A TMS query returned zero for Neuronetics despite $2.8M in
-- source, and LivaNova -- the LARGEST psychiatry spender at $55.2M -- never appeared.
--
-- PERFORMANCE, learned the hard way. The first version called normalize_entity() inline,
-- which runs two regexp_replace per row -- 46M rows x 2 regexes. It ran past 30 minutes
-- and was killed twice. There are only ~2,472 distinct manufacturer names in the whole
-- corpus, so the regex belongs in a dimension that is joined, not an expression evaluated
-- per row. Same output. NOTE: this still takes ~16 minutes on 46M rows -- the dimension
-- join fixed the runaway, not the fundamental cost. If it needs to be faster, make it
-- incremental on Program_Year rather than optimising the aggregate further.
{{ config(materialized='table') }}

with sponsor_dim as (
    -- ~2,472 rows. The expensive normalisation happens exactly this many times.
    select
        sponsor_raw,
        {{ normalize_entity('sponsor_raw') }} as sponsor_key
    from (
        select distinct Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name as sponsor_raw
        from {{ source('cms','op_general') }}
        where Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name <> ''
    )
),
audience as (
    select distinct nullif(trim(cast(npi as varchar)),'') as npi
    from {{ source('derived','op_scored') }}
    where nullif(trim(cast(npi as varchar)),'') is not null
),
payments as (
    select
        nullif(trim(cast(g.Covered_Recipient_NPI as varchar)),'')       as npi,
        g.Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name as sponsor_raw,
        cast(g.Total_Amount_of_Payment_USDollars as double)             as amount,
        cast(g.Program_Year as varchar)                                 as program_year,
        g.Covered_Recipient_Specialty_1                                 as specialty
    from {{ source('cms','op_general') }} g
    where g.Covered_Recipient_NPI is not null
      and g.Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name <> ''
)
select
    p.npi,
    d.sponsor_key,
    min(p.sponsor_raw)             as sponsor_name,
    any_value(p.specialty)         as specialty,
    round(sum(p.amount))           as dollars,
    count(*)                       as payment_rows,
    count(distinct p.program_year) as years_active
from payments p
join audience a   on p.npi = a.npi
join sponsor_dim d on p.sponsor_raw = d.sponsor_raw
group by 1, 2
