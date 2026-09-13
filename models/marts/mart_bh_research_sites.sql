-- Behavioural-health research sites, ranked. Real sites only: central IRBs are excluded
-- by is_real_site, which is why Advarra (117 PIs) does not head this list.
with pi_counts as (
    select site_key, count(distinct npi) as investigators
    from {{ ref('stg_research_investigators') }}
    where is_behavioral_health and site_key <> ''
    group by 1
)
select
    p.site_key,
    min(p.site_name_raw)                         as site_name,
    any_value(p.city)                            as city,
    any_value(p.state)                           as state,
    bool_or(p.is_teaching_hospital)              as is_teaching_hospital,
    coalesce(max(c.investigators),0)             as investigators,
    count(distinct p.sponsor_key)                as sponsors,
    count(distinct p.study_name)                 as studies,
    count(distinct nullif(p.nct_id,''))          as nct_ids,
    round(sum(p.amount))                         as research_dollars,
    count(*)                                     as payment_rows
from {{ ref('stg_research_payments') }} p
left join pi_counts c using (site_key)
where p.is_behavioral_health
  and p.is_real_site
  and p.site_key <> ''
group by 1
order by research_dollars desc
