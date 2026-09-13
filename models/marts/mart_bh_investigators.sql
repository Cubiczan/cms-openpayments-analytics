-- Named principal investigators in behavioural-health research. This is the evidence
-- table: a row here means a sponsor paid for research and named this person on a study.
select
    npi,
    min(first_name)                        as first_name,
    min(last_name)                         as last_name,
    any_value(city)                        as city,
    any_value(state)                       as state,
    min(specialty)                         as specialty,
    count(distinct site_key)               as sites,
    count(distinct sponsor_key)            as sponsors,
    count(distinct study_name)             as studies,
    count(distinct nullif(nct_id,''))      as nct_ids,
    round(sum(amount))                     as research_dollars,
    count(*)                               as payment_rows
from {{ ref('stg_research_investigators') }}
where is_behavioral_health and npi <> ''
group by 1
order by research_dollars desc
