-- THE ADDRESSABLE TABLE. One row per clinician, carrying every axis the segment prompt
-- library asks for, so a marketing question resolves to a filter rather than a project.
--
-- Three layers, deliberately kept distinct:
--   identity  who they are      -- taxonomy, X-Cloud, geography
--   intent    why they'd buy    -- industry money, tier, breadth of relationships
--   evidence  what they've done -- named PI, studies, research dollars
--
-- Evidence is the layer we did not have until this week, and it is the one that separates
-- "looks capable" from "has actually run a trial".
with scored as (
    select
        npi, op_name, city, state, zip,
        x_cloud_abbrev, x_cloud_name, subspecialty, specialty_raw_nucc,
        try_cast(op_total_dollars as double)      as op_total_dollars,
        try_cast(op_kol_dollars as double)        as op_kol_dollars,
        try_cast(op_manufacturer_count as int)    as op_manufacturer_count,
        try_cast(op_score as double)              as op_score,
        op_intent_tier, score_basis
    from {{ source('derived','op_scored') }}
),
mfr as (
    select npi,
           count(distinct sponsor_key)                                   as sponsor_count_full,
           round(sum(dollars))                                           as sponsor_dollars_full,
           string_agg(distinct sponsor_name, ' | ' order by sponsor_name) as sponsor_list_full
    from {{ ref('mart_npi_manufacturers') }}
    group by 1
)
select
    -- identity
    s.npi, s.op_name, s.city, s.state, s.zip,
    s.x_cloud_abbrev, s.x_cloud_name, s.subspecialty, s.specialty_raw_nucc,
    -- intent
    s.op_score, s.op_intent_tier, s.op_total_dollars, s.op_kol_dollars,
    coalesce(m.sponsor_count_full,  s.op_manufacturer_count, 0) as sponsor_count,
    coalesce(m.sponsor_dollars_full, s.op_total_dollars, 0)     as sponsor_dollars,
    m.sponsor_list_full                                          as sponsors,
    -- evidence
    i.npi is not null                     as is_named_investigator,
    coalesce(i.studies, 0)                as research_studies,
    coalesce(i.sponsors, 0)               as research_sponsors,
    coalesce(i.research_dollars, 0)       as research_dollars,
    -- segment flags: these correspond 1:1 to patterns in SEGMENT_PROMPT_LIBRARY.md
    s.op_intent_tier in ('Tier 1','Tier 2')                    as seg_kol,
    coalesce(m.sponsor_count_full,0) >= 5                      as seg_broad_industry,
    i.npi is not null                                          as seg_trial_active,
    (i.npi is null and s.op_intent_tier in ('Tier 1','Tier 2')) as seg_untapped_bench,
    s.score_basis = 'subspecialty'                             as seg_scored_in_cohort
from scored s
left join mfr m                          on s.npi = m.npi
left join {{ ref('mart_bh_investigators') }} i on s.npi = i.npi
