-- The site-capacity question, as a model rather than an ad-hoc query.
--
-- Measured 10 Sep: a Tier 1 psychiatrist is 20x more likely to be a named PI than a
-- Tier 4 (12.3% vs 0.6%), which validates the intent score as a RANKING signal. But the
-- untapped bench is smaller than the existing PI pool in every major state, which is the
-- open question for the first three sponsor conversations.
with scored as (
    select
        npi, op_name, city, state, x_cloud_abbrev, subspecialty,
        try_cast(op_total_dollars as double) as op_total_dollars,
        op_intent_tier
    from {{ source('derived','op_scored') }}
    where x_cloud_abbrev = 'PSY'
)
select
    s.npi, s.op_name, s.city, s.state, s.subspecialty,
    s.op_intent_tier,
    s.op_total_dollars,
    i.npi is not null                       as is_named_pi,
    coalesce(i.studies,0)                   as research_studies,
    coalesce(i.sponsors,0)                  as research_sponsors,
    coalesce(i.research_dollars,0)          as research_dollars,
    case
      when i.npi is null and s.op_intent_tier in ('Tier 1','Tier 2') then 'untapped_bench'
      when i.npi is not null                                          then 'active_investigator'
      else 'low_signal'
    end                                     as capacity_segment
from scored s
left join {{ ref('mart_bh_investigators') }} i on s.npi = i.npi
